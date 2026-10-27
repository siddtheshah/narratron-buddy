// Offline CEL-Go experiment. Single request at a time; one compiled package.
package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/rand"
	"encoding/json"
	"fmt"
	"math"
	"math/big"
	"os"
	"runtime"
	"runtime/debug"
	"strconv"
	"strings"
	"time"

	"cel.dev/cel-go/cel"
	"cel.dev/cel-go/common/types"
	"cel.dev/cel-go/common/types/ref"
	"cel.dev/cel-go/common/types/traits"
)

const maxFrame = 4 * 1024 * 1024
const maxValue = 131072

type binding struct{ Name, Expr string }
type function struct {
	Parameters []string
	Expr       string
}
type transition struct {
	Actor, Permission, Description, Available, Guard, Result string
	Parameters                                               map[string]interface{}
	Bindings                                                 []binding
	Triggering                                               bool
	OnError                                                  json.RawMessage `json:"on_error"`
}
type viewAction struct {
	Name, Arguments, Confirmation string
	Permission                    *string
}
type view struct {
	Kind     string
	Props    map[string]string
	Repeat   *string
	Binding  string
	Children []view
	Action   *viewAction
}
type definition struct {
	Initial, Constants map[string]interface{}
	Functions          map[string]function
	Derived            []binding
	Invariants         []string
	ModelState         *string `json:"model_state"`
	Transitions        map[string]transition
	View               view
}
type rendered struct {
	Kind     string                 `json:"kind"`
	Props    map[string]interface{} `json:"props"`
	Children []rendered             `json:"children"`
	Action   interface{}            `json:"action"`
}
type step struct {
	Name      string
	Arguments map[string]interface{}
}
type request struct {
	Op, Source, Actor string
	Definition        definition
	Sources           map[string][]string
	Random            []string
	Assets            map[string]rendered
	State             map[string]interface{}
	Steps             []step
	Context           map[string]interface{}
	TimeoutMS         int `json:"timeout_ms"`
}
type prepared struct {
	State      map[string]interface{}   `json:"state"`
	View       []rendered               `json:"view"`
	ModelState interface{}              `json:"model_state"`
	Actions    map[string]interface{}   `json:"actions"`
	Candidates []map[string]interface{} `json:"candidates"`
}
type metrics struct {
	OperationNS  int64 `json:"operation_ns"`
	ConversionNS int64 `json:"conversion_ns"`
	Evaluations  int   `json:"evaluations"`
	HelperCalls  int   `json:"helper_calls"`
	CacheHits    int   `json:"cache_hits"`
}
type response struct {
	Result  interface{} `json:"result"`
	Error   string      `json:"error"`
	Metrics metrics     `json:"metrics"`
}
type scope struct {
	values map[string]interface{}
	ids    map[string]int
}
type execution struct {
	ctx           context.Context
	pkg           *compiled
	cache         map[string]ref.Val
	nextID, nodes int
	stats         metrics
}
type compiled struct {
	def          definition
	programs     map[string]cel.Program
	dependencies map[string][]string
	random       map[string]bool
	assets       map[string]rendered
	active       *execution
}

// Preserve JSON integers (including int64 boundaries) instead of decoding them
// as float64. This also applies recursively to package constants and actions.
func normalize(value interface{}) (interface{}, error) {
	switch v := value.(type) {
	case json.Number:
		if !strings.ContainsAny(string(v), ".eE") {
			return v.Int64()
		}
		return v.Float64()
	case []interface{}:
		for i := range v {
			n, err := normalize(v[i])
			if err != nil {
				return nil, err
			}
			v[i] = n
		}
	case map[string]interface{}:
		for k := range v {
			n, err := normalize(v[k])
			if err != nil {
				return nil, err
			}
			v[k] = n
		}
	}
	return value, nil
}

func compile(req request) (*compiled, error) {
	p := &compiled{def: req.Definition, programs: map[string]cel.Program{}, dependencies: req.Sources, random: map[string]bool{}, assets: req.Assets}
	for _, s := range req.Random {
		p.random[s] = true
	}
	opts := []cel.EnvOption{}
	for name, f := range p.def.Functions {
		name, f := name, f
		args := make([]*cel.Type, len(f.Parameters))
		for i := range args {
			args[i] = cel.DynType
		}
		opts = append(opts, cel.Function(name, cel.Overload("game_"+name, args, cel.DynType, cel.FunctionBinding(func(values ...ref.Val) ref.Val {
			e := p.active
			if e == nil {
				return types.NewErr("no active operation")
			}
			e.stats.HelperCalls++
			vars := map[string]interface{}{}
			for i, value := range values {
				v, err := e.helperValue(value)
				if err != nil {
					return types.NewErr("%s", err)
				}
				vars[f.Parameters[i]] = v
			}
			vars["constants"] = p.def.Constants
			v, err := e.eval(f.Expr, e.newScope(vars))
			if err != nil {
				return types.NewErr("%s", err)
			}
			plain, err := e.helperValue(v)
			if err != nil {
				return types.NewErr("%s", err)
			}
			return types.DefaultTypeAdapter.NativeToValue(plain)
		}))))
	}
	opts = append(opts, cel.Function("random_int", cel.Overload("game_random_int", []*cel.Type{cel.IntType, cel.IntType}, cel.IntType,
		cel.BinaryBinding(func(a, b ref.Val) ref.Val {
			lo, ok := a.(types.Int)
			if !ok {
				return types.NewErr("random_int requires integer bounds")
			}
			hi, ok := b.(types.Int)
			if !ok || lo > hi {
				return types.NewErr("random_int invalid bounds")
			}
			span := new(big.Int).Sub(big.NewInt(int64(hi)), big.NewInt(int64(lo)))
			span.Add(span, big.NewInt(1))
			n, err := rand.Int(rand.Reader, span)
			if err != nil {
				return types.NewErr("%s", err)
			}
			n.Add(n, big.NewInt(int64(lo)))
			return types.Int(n.Int64())
		}))))
	env, err := cel.NewEnv(opts...)
	if err != nil {
		return nil, err
	}
	for source := range req.Sources {
		// The parent performs the application's syntax/function restrictions.
		// Parse-only retains the package's dynamically scoped JSON variables.
		ast, issues := env.Parse(source)
		if issues != nil && issues.Err() != nil {
			return nil, issues.Err()
		}
		program, err := env.Program(ast, cel.InterruptCheckFrequency(100))
		if err != nil {
			return nil, err
		}
		p.programs[source] = program
	}
	return p, nil
}

func (e *execution) newScope(values map[string]interface{}) scope {
	s := scope{values: values, ids: map[string]int{}}
	for name := range values {
		e.nextID++
		s.ids[name] = e.nextID
	}
	return s
}
func (e *execution) extend(s scope, name string, value interface{}) scope {
	values, ids := map[string]interface{}{}, map[string]int{}
	for k, v := range s.values {
		values[k] = v
		ids[k] = s.ids[k]
	}
	e.nextID++
	values[name] = value
	ids[name] = e.nextID
	return scope{values: values, ids: ids}
}
func (e *execution) eval(source string, s scope) (ref.Val, error) {
	if err := e.ctx.Err(); err != nil {
		return nil, err
	}
	program, exists := e.pkg.programs[source]
	if !exists {
		return nil, fmt.Errorf("uncompiled expression")
	}
	key := source
	for _, dep := range e.pkg.dependencies[source] {
		key += "\x00" + dep + "=" + strconv.Itoa(s.ids[dep])
	}
	if !e.pkg.random[source] {
		if v, ok := e.cache[key]; ok {
			e.stats.CacheHits++
			return v, nil
		}
	}
	e.stats.Evaluations++
	v, _, err := program.ContextEval(e.ctx, s.values)
	// Check the shared context even when a CEL boolean masks a helper error.
	if deadline := e.ctx.Err(); deadline != nil {
		return nil, deadline
	}
	if err != nil {
		return nil, err
	}
	if types.IsError(v) {
		return nil, fmt.Errorf("%s", v)
	}
	plain, err := e.convert(v)
	if err != nil {
		return nil, err
	}
	v = types.DefaultTypeAdapter.NativeToValue(plain)
	if !e.pkg.random[source] {
		e.cache[key] = v
	}
	return v, nil
}

func plainValue(value ref.Val, remaining *int, depth int) (interface{}, error) {
	*remaining--
	if *remaining < 0 || depth > 64 {
		return nil, fmt.Errorf("value size or nesting budget exceeded")
	}
	var result interface{}
	switch v := value.(type) {
	case types.Null:
		result = nil
	case types.Bool:
		result = bool(v)
	case types.String:
		if len(v) > *remaining {
			return nil, fmt.Errorf("value size budget exceeded")
		}
		result = string(v)
	case types.Int:
		result = int64(v)
	case types.Uint:
		// The application's JSON boundary erases uint into a Python integer.
		// Keep large uint results representable on the wire as well.
		if uint64(v) <= math.MaxInt64 {
			result = int64(v)
		} else {
			result = uint64(v)
		}
	case types.Double:
		if math.IsNaN(float64(v)) || math.IsInf(float64(v), 0) {
			return nil, fmt.Errorf("non-finite result")
		}
		result = float64(v)
	case traits.Lister:
		n := int(v.Size().(types.Int))
		*remaining -= n + 2
		if *remaining < 0 {
			return nil, fmt.Errorf("value size budget exceeded")
		}
		items := make([]interface{}, n)
		for i := range items {
			item, err := plainValue(v.Get(types.Int(i)), remaining, depth+1)
			if err != nil {
				return nil, err
			}
			items[i] = item
		}
		return items, nil
	case traits.Mapper:
		*remaining -= int(v.Size().(types.Int))*2 + 2
		if *remaining < 0 {
			return nil, fmt.Errorf("value size budget exceeded")
		}
		items := map[string]interface{}{}
		it := v.Iterator()
		for it.HasNext() == types.True {
			key := it.Next()
			name, ok := key.(types.String)
			if !ok {
				return nil, fmt.Errorf("map keys must be strings")
			}
			_, err := plainValue(name, remaining, depth+1)
			if err != nil {
				return nil, err
			}
			item, err := plainValue(v.Get(key), remaining, depth+1)
			if err != nil {
				return nil, err
			}
			items[string(name)] = item
		}
		return items, nil
	default:
		return nil, fmt.Errorf("non-JSON CEL value: %v", value)
	}
	data, err := json.Marshal(result)
	if err != nil {
		return nil, err
	}
	*remaining -= len(data)
	if *remaining < 0 {
		return nil, fmt.Errorf("value size budget exceeded")
	}
	return result, nil
}
func (e *execution) convert(v ref.Val) (interface{}, error) {
	start := clockNS()
	defer func() { e.stats.ConversionNS += clockNS() - start }()
	budget := maxValue
	return plainValue(v, &budget, 0)
}

// Package helpers cross the same JSON-to-signed-int boundary as Python
// callbacks. A direct uint argument to random_int still stays a CEL uint and
// fails its typed overload before any JSON conversion.
func helperNative(value interface{}) (interface{}, error) {
	switch v := value.(type) {
	case uint64:
		if v > math.MaxInt64 {
			return nil, fmt.Errorf("integer overflow at helper boundary")
		}
		return int64(v), nil
	case []interface{}:
		for i := range v {
			item, err := helperNative(v[i])
			if err != nil {
				return nil, err
			}
			v[i] = item
		}
	case map[string]interface{}:
		for k := range v {
			item, err := helperNative(v[k])
			if err != nil {
				return nil, err
			}
			v[k] = item
		}
	}
	return value, nil
}
func (e *execution) helperValue(v ref.Val) (interface{}, error) {
	value, err := e.convert(v)
	if err != nil {
		return nil, err
	}
	return helperNative(value)
}
func (e *execution) value(source string, s scope) (interface{}, error) {
	v, err := e.eval(source, s)
	if err != nil {
		return nil, err
	}
	return e.convert(v)
}
func (e *execution) bindings(bindings []binding, s scope) (scope, error) {
	for _, b := range bindings {
		if _, exists := s.values[b.Name]; exists {
			return s, fmt.Errorf("binding shadows %s", b.Name)
		}
		v, err := e.eval(b.Expr, s)
		if err != nil {
			return s, err
		}
		s = e.extend(s, b.Name, v)
	}
	return s, nil
}
func (e *execution) stateScope(state map[string]interface{}) (scope, error) {
	return e.bindings(e.pkg.def.Derived, e.newScope(map[string]interface{}{"state": state, "constants": e.pkg.def.Constants, "initial": e.pkg.def.Initial}))
}
func (e *execution) validate(s scope) error {
	for _, source := range e.pkg.def.Invariants {
		v, err := e.eval(source, s)
		if err != nil {
			return err
		}
		if v != types.True {
			return fmt.Errorf("game state invariant failed")
		}
	}
	return nil
}
func (e *execution) render(node view, s scope) ([]rendered, error) {
	scopes := []scope{s}
	if node.Repeat != nil {
		v, err := e.eval(*node.Repeat, s)
		if err != nil {
			return nil, err
		}
		list, ok := v.(traits.Lister)
		if !ok || list.Size().(types.Int) > 256 {
			return nil, fmt.Errorf("invalid view repetition")
		}
		if _, exists := s.values[node.Binding]; exists {
			return nil, fmt.Errorf("repeat binding shadows scope")
		}
		scopes = []scope{}
		for i := types.Int(0); i < list.Size().(types.Int); i++ {
			scopes = append(scopes, e.extend(s, node.Binding, list.Get(i)))
		}
	}
	out := []rendered{}
	for _, current := range scopes {
		if err := e.ctx.Err(); err != nil {
			return nil, err
		}
		e.nodes++
		if e.nodes > 1024 {
			return nil, fmt.Errorf("view node budget exceeded")
		}
		r := rendered{Kind: node.Kind, Props: map[string]interface{}{}, Children: []rendered{}}
		for name, source := range node.Props {
			v, err := e.value(source, current)
			if err != nil {
				return nil, err
			}
			r.Props[name] = v
		}
		if node.Action != nil {
			name, err := e.value(node.Action.Name, current)
			if err != nil {
				return nil, err
			}
			n, ok := name.(string)
			if !ok {
				return nil, fmt.Errorf("action name must be string")
			}
			t, exists := e.pkg.def.Transitions[n]
			if !exists || t.Actor == "narratron" {
				return nil, fmt.Errorf("invalid view action")
			}
			args, err := e.value(node.Action.Arguments, current)
			if err != nil {
				return nil, err
			}
			if _, ok := args.(map[string]interface{}); !ok {
				return nil, fmt.Errorf("action arguments must be map")
			}
			permission := "orator"
			if t.Permission == "contributor" || t.Actor == "contributor" {
				permission = "contributor"
			}
			if node.Action.Permission != nil {
				permission = *node.Action.Permission
			}
			r.Action = map[string]interface{}{"name": name, "arguments": args, "confirmation": node.Action.Confirmation, "permission": permission}
		}
		for _, child := range node.Children {
			children, err := e.render(child, current)
			if err != nil {
				return nil, err
			}
			r.Children = append(r.Children, children...)
		}
		if r.Kind == "image" {
			r.Kind = "svg"
		}
		if r.Kind == "svg" {
			for _, key := range []string{"src", "href", "asset"} {
				raw, exists := r.Props[key]
				if !exists {
					continue
				}
				delete(r.Props, key)
				if raw != nil && raw != "" {
					src, ok := raw.(string)
					if !ok {
						return nil, fmt.Errorf("asset path must be string")
					}
					src = strings.TrimPrefix(strings.TrimSpace(src), "game/")
					asset, ok := e.pkg.assets[src]
					if !ok {
						return nil, fmt.Errorf("unknown SVG asset %s", src)
					}
					props := map[string]interface{}{}
					for k, v := range asset.Props {
						props[k] = v
					}
					for k, v := range r.Props {
						props[k] = v
					}
					props["src"] = src
					r.Props = props
					r.Children = append(append([]rendered{}, asset.Children...), r.Children...)
				}
				break
			}
		}
		out = append(out, r)
	}
	return out, nil
}
func (e *execution) prepare(state map[string]interface{}) (prepared, error) {
	out := prepared{State: state, Actions: map[string]interface{}{}, Candidates: []map[string]interface{}{}}
	s, err := e.stateScope(state)
	if err != nil {
		return out, err
	}
	if err := e.validate(s); err != nil {
		return out, err
	}
	e.nodes = 0
	out.View, err = e.render(e.pkg.def.View, s)
	if err != nil {
		return out, err
	}
	out.ModelState = state
	if e.pkg.def.ModelState != nil {
		out.ModelState, err = e.value(*e.pkg.def.ModelState, s)
		if err != nil {
			return out, err
		}
	}
	for name, t := range e.pkg.def.Transitions {
		if t.Actor != "narratron" {
			continue
		}
		v, err := e.eval(t.Available, s)
		if err != nil {
			return out, err
		}
		if v == types.True {
			out.Actions[name] = map[string]interface{}{"description": t.Description, "parameters": t.Parameters}
		} else if v != types.False {
			return out, fmt.Errorf("availability must be boolean")
		}
	}
	return out, nil
}
func (e *execution) operate(req request) (interface{}, error) {
	if req.Op == "eval" {
		return e.value(req.Source, e.newScope(req.Context))
	}
	state := req.State
	if req.Op == "load" {
		state = e.pkg.def.Initial
	}
	candidates := []map[string]interface{}{}
	for _, action := range req.Steps {
		t, exists := e.pkg.def.Transitions[action.Name]
		if !exists {
			return nil, fmt.Errorf("unknown transition")
		}
		s, err := e.stateScope(state)
		if err != nil {
			return nil, err
		}
		s = e.extend(s, "action", action.Arguments)
		guard, err := e.eval(t.Guard, s)
		if err != nil {
			return nil, err
		}
		if guard != types.True {
			return nil, fmt.Errorf("This action is not permitted in the current game state.")
		}
		s, err = e.bindings(t.Bindings, s)
		if err != nil {
			return nil, err
		}
		value, err := e.value(t.Result, s)
		if err != nil {
			return nil, err
		}
		state, exists = value.(map[string]interface{})
		if !exists {
			return nil, fmt.Errorf("transition must return state map")
		}
		candidateScope, err := e.stateScope(state)
		if err != nil {
			return nil, err
		}
		if err := e.validate(candidateScope); err != nil {
			return nil, err
		}
		candidates = append(candidates, state)
	}
	out, err := e.prepare(state)
	out.Candidates = candidates
	return out, err
}

func main() {
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 4096), maxFrame)
	writer := bufio.NewWriter(os.Stdout)
	var pkg *compiled
	for scanner.Scan() {
		start := clockNS()
		resp := response{}
		req := request{}
		// UseNumber preserves integer boundaries AND integral double inputs.
		decoder := json.NewDecoder(bytes.NewReader(scanner.Bytes()))
		decoder.UseNumber()
		err := decoder.Decode(&req)
		if err == nil {
			for _, value := range []interface{}{req.Definition.Initial, req.Definition.Constants, req.State, req.Context} {
				_, err = normalize(value)
				if err != nil {
					break
				}
			}
			for _, s := range req.Steps {
				if err == nil {
					_, err = normalize(s.Arguments)
				}
			}
			for _, t := range req.Definition.Transitions {
				if err == nil {
					_, err = normalize(t.Parameters)
				}
			}
		}
		if err == nil && req.Op == "ping" {
			version := "unknown"
			if info, ok := debug.ReadBuildInfo(); ok {
				for _, dependency := range info.Deps {
					if dependency.Path == "cel.dev/cel-go" {
						version = dependency.Version
					}
				}
			}
			resp.Result = map[string]string{"go_version": runtime.Version(), "cel_go_version": version, "metrics_clock": metricsClock}
		} else if err == nil {
			if req.Op == "load" {
				pkg, err = compile(req)
			}
			if err == nil && pkg == nil {
				err = fmt.Errorf("no package loaded")
			}
			if err == nil {
				if req.TimeoutMS < 1 || req.TimeoutMS > 30000 {
					err = fmt.Errorf("invalid operation timeout")
				} else {
					ctx, cancel := context.WithTimeout(context.Background(), time.Duration(req.TimeoutMS)*time.Millisecond)
					e := &execution{ctx: ctx, pkg: pkg, cache: map[string]ref.Val{}}
					pkg.active = e
					resp.Result, err = e.operate(req)
					resp.Metrics = e.stats
					pkg.active = nil
					cancel()
				}
			}
		}
		if err != nil {
			resp.Result = nil
			resp.Error = err.Error()
		}
		resp.Metrics.OperationNS = clockNS() - start
		data, err := json.Marshal(resp)
		if err != nil || len(data) >= maxFrame {
			data = []byte(`{"error":"response size budget exceeded","result":null,"metrics":{}}`)
		}
		if _, err := writer.Write(append(data, '\n')); err != nil {
			return
		}
		if err := writer.Flush(); err != nil {
			return
		}
	}
}
