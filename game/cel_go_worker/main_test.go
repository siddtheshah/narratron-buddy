package main

import (
	"context"
	"strings"
	"testing"

	"cel.dev/cel-go/common/types"
	"cel.dev/cel-go/common/types/ref"
)

func TestAggregateConversions(t *testing.T) {
	e := &execution{}
	for i := 0; i < 100; i++ {
		_, err := e.convert(types.String(strings.Repeat("x", 100000)))
		if err != nil {
			if e.convertedBytes < maxFrame || e.convertedBytes > maxFrame+100010 {
				t.Fatalf("unexpected budget accounting: %d", e.convertedBytes)
			}
			if e.exhausted == nil {
				t.Fatal("aggregate exhaustion must persist")
			}
			return
		}
	}
	t.Fatal("individually valid values exceeded aggregate budget")
}

func TestAggregateSVGAndModelState(t *testing.T) {
	source := "'asset.svg'"
	pkg, err := compile(request{
		Sources: map[string][]string{source: {}, "state": {}},
		Assets:  map[string]rendered{"asset.svg": {Kind: "svg"}},
		Definition: definition{ModelState: stringPointer("state"), View: view{
			Kind: "svg", Props: map[string]string{"src": source},
		}},
	})
	if err != nil {
		t.Fatal(err)
	}
	// Simulate prior candidate conversions near the operation limit.
	e := &execution{ctx: context.Background(), pkg: pkg, cache: map[string]ref.Val{},
		costLimit: defaultCostLimit, convertedBytes: maxFrame - 10000}
	pkg.active = e
	pkg.assetBytes["asset.svg"] = 9000
	_, err = e.prepare(map[string]interface{}{"text": strings.Repeat("x", 2000)})
	if err == nil || !strings.Contains(err.Error(), "aggregate output") {
		t.Fatalf("SVG and model_state must share candidate budget: %v", err)
	}
}

func stringPointer(value string) *string { return &value }
