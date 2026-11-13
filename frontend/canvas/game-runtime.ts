// A data-only interpreter for the server's compiled CEL subset. Never executes package JS.

export type GamePrimitive = string | number | boolean | null;
export type GameValue = GamePrimitive | GameValue[] | GameRecord;
export interface GameRecord {
  [key: string]: GameValue;
}

export type CelAstArg = GamePrimitive | CelAstNode;
export interface CelAstNode extends ReadonlyArray<CelAstArg> {}

export type GamePermission = 'orator' | 'contributor';

export interface GameBinding {
  readonly name: string;
  readonly expr: string;
}

export interface GameFunctionDefinition {
  readonly parameters: readonly string[];
  readonly expr: string;
}

export interface GameTransitionClient {
  readonly actor?: string;
  readonly permission?: GamePermission;
  readonly guard: string;
  readonly bindings: readonly GameBinding[];
  readonly result: string;
  readonly triggering?: boolean;
  readonly preview?: boolean;
}

export interface GameViewActionDefinition {
  readonly name: string;
  readonly arguments: string;
  readonly confirmation?: string;
  readonly permission?: GamePermission | null;
}

export interface GameViewNodeDefinition {
  readonly kind: string;
  readonly props: Readonly<Record<string, string>>;
  readonly repeat: string | null;
  readonly binding: string;
  readonly children: readonly GameViewNodeDefinition[];
  readonly action: GameViewActionDefinition | null;
}

export interface RenderedGameAction {
  readonly name: string;
  readonly arguments: GameRecord;
  readonly confirmation?: string;
  readonly permission?: GamePermission;
}

export interface RenderedGameNode {
  readonly kind: string;
  readonly props: GameRecord;
  readonly children: readonly RenderedGameNode[];
  readonly action: RenderedGameAction | null;
}

export interface GameAssetNode {
  readonly kind?: string;
  readonly props: Readonly<Record<string, GameValue>>;
  readonly children: readonly RenderedGameNode[];
}

export interface GameClientProgram {
  readonly expressions: Readonly<Record<string, CelAstNode>>;
  readonly initial: GameRecord;
  readonly constants: GameRecord;
  readonly functions: Readonly<Record<string, GameFunctionDefinition>>;
  readonly derived: readonly GameBinding[];
  readonly invariants: readonly string[];
  readonly transitions: Readonly<Record<string, GameTransitionClient>>;
  readonly view: GameViewNodeDefinition;
  readonly assets: Readonly<Record<string, GameAssetNode>>;
}

export interface LocalGameActionInput {
  readonly name: string;
  readonly arguments: GameRecord;
  readonly username?: string;
}

export interface LocalGameSnapshot {
  readonly state: GameRecord;
  readonly view: RenderedGameNode[];
}

export interface LocalGameRuntime {
  apply(state: GameRecord, action: LocalGameActionInput): LocalGameSnapshot;
}

function isRecord(value: GameValue): value is GameRecord {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function equal(left: GameValue, right: GameValue): boolean {
  if (left === right) return true;
  if (left === null || right === null || typeof left !== 'object' || typeof right !== 'object') {
    return false;
  }
  if (Array.isArray(left) || Array.isArray(right)) {
    if (!Array.isArray(left) || !Array.isArray(right) || left.length !== right.length) {
      return false;
    }
    return left.every((item, index) => equal(item, right[index]));
  }
  const keys = Object.keys(left);
  return (
    keys.length === Object.keys(right).length &&
    keys.every((key) => Object.hasOwn(right, key) && equal(left[key], right[key]))
  );
}

function size(value: GameValue): number {
  if (typeof value === 'string') return Array.from(value).length;
  if (Array.isArray(value)) return value.length;
  if (value !== null && typeof value === 'object') return Object.keys(value).length;
  throw new Error('Expected a string, list, or map.');
}

function boolean(value: GameValue): boolean {
  if (typeof value !== 'boolean') throw new Error('Expected a boolean.');
  return value;
}

function numberValue(value: GameValue): number {
  if (typeof value !== 'number') throw new Error('Expected a number.');
  return value;
}

function asNode(arg: CelAstArg | undefined): CelAstNode {
  if (!Array.isArray(arg)) throw new Error('Unsupported game expression.');
  return arg as CelAstNode;
}

function asString(arg: CelAstArg | GameValue | undefined): string {
  if (typeof arg !== 'string') throw new Error('Expected a string.');
  return arg;
}

function asRecord(value: GameValue): GameRecord {
  if (!isRecord(value)) throw new Error('Expected a map.');
  return value;
}

function asArray(value: GameValue): GameValue[] {
  if (!Array.isArray(value)) throw new Error('Expected a list.');
  return value;
}

function read(value: GameValue, key: GameValue): GameValue {
  if (Array.isArray(value)) {
    if (typeof key === 'number') {
      if (!Object.hasOwn(value, key)) throw new Error('Unknown game field.');
      return value[key];
    }
    if (typeof key === 'string' && Object.hasOwn(value, key)) {
      const index = Number(key);
      if (Number.isInteger(index)) return value[index];
    }
    throw new Error('Unknown game field.');
  }
  if (!isRecord(value)) throw new Error('Unknown game field.');
  const prop = String(key);
  if (!Object.hasOwn(value, prop)) throw new Error('Unknown game field.');
  return value[prop];
}

function readMapEntry<T>(record: Readonly<Record<string, T>>, key: string): T {
  if (!Object.hasOwn(record, key)) throw new Error('Unknown game field.');
  return record[key];
}

class InvalidGameInput extends Error {}

export function createLocalGame(program: GameClientProgram): LocalGameRuntime {
  let remaining = 0;
  let deadline = 0;
  let nodes = 0;

  function resetBudget(): void {
    remaining = 500000;
    deadline = performance.now() + 1000;
    nodes = 0;
  }

  function charge(): void {
    if (--remaining < 0 || (remaining % 256 === 0 && performance.now() > deadline)) {
      throw new Error('Game preview exceeded its work budget.');
    }
  }

  function evaluate(source: string, scope: GameRecord): GameValue {
    return visit(readMapEntry(program.expressions, source), scope);
  }

  function visit(tree: CelAstNode, scope: GameRecord): GameValue {
    charge();
    const result = interpret(tree, scope);
    if (typeof result === 'number' && !Number.isSafeInteger(result)) {
      throw new Error('Game preview exceeds integer precision.');
    }
    return result;
  }

  function interpret(tree: CelAstNode, scope: GameRecord): GameValue {
    const [kind, ...args] = tree;
    const value = (index: number): GameValue => visit(asNode(args[index]), scope);

    switch (kind) {
      case 'literal': {
        const literal = args[0];
        if (literal === undefined || (typeof literal === 'object' && literal !== null)) {
          throw new Error('Unsupported game expression.');
        }
        return literal;
      }
      case 'ident':
        return read(scope, asString(args[0]));
      case 'expr':
        return boolean(value(0)) ? value(1) : value(2);
      case 'conditionaland':
        return boolean(value(0)) && boolean(value(1));
      case 'conditionalor':
        return boolean(value(0)) || boolean(value(1));
      case 'unary': {
        const unaryOp = asNode(args[0])[0];
        return unaryOp === 'unary_not' ? !boolean(value(1)) : -numberValue(value(1));
      }
      case 'member_dot':
        return read(value(0), asString(args[1]));
      case 'member_index':
        return read(value(0), value(1));
      case 'list_lit':
        return args.length ? visit(asNode(args[0]), scope) : [];
      case 'exprlist':
        return args.map((arg) => visit(asNode(arg), scope));
      case 'map_lit': {
        const entries = args.length ? asNode(args[0]).slice(1) : [];
        const result = Object.create(null) as GameRecord;
        for (let i = 0; i < entries.length; i += 2) {
          const mapKey = String(visit(asNode(entries[i]), scope));
          result[mapKey] = visit(asNode(entries[i + 1]), scope);
        }
        return result;
      }
      case 'ident_arg': {
        const name = asString(args[0]);
        const values = args.length > 1 ? asArray(visit(asNode(args[1]), scope)) : [];
        if (name === 'size') return size(values[0]);
        if (name === 'string') return String(values[0]);
        if (name === 'int') {
          const raw = values[0];
          if (typeof raw === 'string' && !/^[+-]?\d+$/.test(raw)) {
            throw new Error('Invalid integer.');
          }
          const num = Math.trunc(Number(raw));
          if (!Number.isSafeInteger(num)) throw new Error('Invalid integer.');
          return num;
        }
        if (name === 'bool') {
          const raw = values[0];
          if (raw === true || raw === 'true') return true;
          if (raw === false || raw === 'false') return false;
          throw new Error('Invalid boolean.');
        }
        const fn = readMapEntry(program.functions, name);
        if (values.length !== fn.parameters.length) {
          throw new Error('Invalid function arguments.');
        }
        const fnBindings = Object.fromEntries(
          fn.parameters.map((param, index) => [param, values[index]]),
        ) as GameRecord;
        return evaluate(fn.expr, { ...fnBindings, constants: program.constants });
      }
      case 'member_dot_arg': {
        const collection = value(0);
        const method = asString(args[1]);
        if (method === 'size') return size(collection);
        if (!Array.isArray(collection)) throw new Error('Expected a list.');
        const parameters = asNode(args[2]).slice(1);
        const variable = asString(asNode(parameters[0])[1]);
        const body = asNode(parameters[1]);
        const callback = (item: GameValue): GameValue =>
          visit(body, { ...scope, [variable]: item });
        if (method === 'map') return collection.map(callback);
        if (method === 'filter') return collection.filter((item) => boolean(callback(item)));
        if (method === 'exists') return collection.some((item) => boolean(callback(item)));
        if (method === 'all') return collection.every((item) => boolean(callback(item)));
        throw new Error('Unsupported game method.');
      }
      case 'relation':
      case 'addition':
      case 'multiplication': {
        const pair = asNode(args[0]);
        const operator = asString(pair[0]);
        const left = visit(asNode(pair[1]), scope);
        const right = value(1);
        switch (operator) {
          case 'relation_eq':
            return equal(left, right);
          case 'relation_ne':
            return !equal(left, right);
          case 'relation_lt':
            if (typeof left === 'number' && typeof right === 'number') return left < right;
            if (typeof left === 'string' && typeof right === 'string') return left < right;
            if (typeof left === 'boolean' && typeof right === 'boolean') return left < right;
            throw new Error('Incompatible comparison operands.');
          case 'relation_le':
            if (typeof left === 'number' && typeof right === 'number') return left <= right;
            if (typeof left === 'string' && typeof right === 'string') return left <= right;
            if (typeof left === 'boolean' && typeof right === 'boolean') return left <= right;
            throw new Error('Incompatible comparison operands.');
          case 'relation_gt':
            if (typeof left === 'number' && typeof right === 'number') return left > right;
            if (typeof left === 'string' && typeof right === 'string') return left > right;
            if (typeof left === 'boolean' && typeof right === 'boolean') return left > right;
            throw new Error('Incompatible comparison operands.');
          case 'relation_ge':
            if (typeof left === 'number' && typeof right === 'number') return left >= right;
            if (typeof left === 'string' && typeof right === 'string') return left >= right;
            if (typeof left === 'boolean' && typeof right === 'boolean') return left >= right;
            throw new Error('Incompatible comparison operands.');
          case 'relation_in':
            if (Array.isArray(right)) return right.some((item) => equal(left, item));
            if (isRecord(right)) return Object.hasOwn(right, String(left));
            throw new Error('Expected a list or map.');
          case 'addition_add':
            if (Array.isArray(left)) {
              if (!Array.isArray(right)) throw new Error('Expected a list.');
              return left.concat(right);
            }
            if (typeof left === 'string' && typeof right === 'string') return left + right;
            if (typeof left === 'number' && typeof right === 'number') return left + right;
            throw new Error('Incompatible addition operands.');
          case 'addition_sub':
            return numberValue(left) - numberValue(right);
          case 'multiplication_mul':
            return numberValue(left) * numberValue(right);
          case 'multiplication_div': {
            const divisor = numberValue(right);
            if (!divisor) throw new Error('Division by zero.');
            return Math.trunc(numberValue(left) / divisor);
          }
          case 'multiplication_mod': {
            const divisor = numberValue(right);
            if (!divisor) throw new Error('Division by zero.');
            return numberValue(left) % divisor;
          }
          default:
            throw new Error('Unsupported game operator.');
        }
      }
      default:
        throw new Error('Unsupported game expression.');
    }
  }

  function bindings(definitions: readonly GameBinding[], scope: GameRecord): void {
    for (const binding of definitions) {
      if (Object.hasOwn(scope, binding.name)) throw new Error('Game binding shadows a name.');
      scope[binding.name] = evaluate(binding.expr, scope);
    }
  }

  function context(state: GameRecord, username = ''): GameRecord {
    const scope: GameRecord = {
      state,
      initial: program.initial,
      constants: program.constants,
      user: { username },
    };
    bindings(program.derived, scope);
    return scope;
  }

  function validate(scope: GameRecord): void {
    if (!program.invariants.every((expr) => boolean(evaluate(expr, scope)))) {
      throw new Error('Game invariant failed.');
    }
  }

  function render(
    node: GameViewNodeDefinition,
    scope: GameRecord,
    depth = 0,
  ): RenderedGameNode[] {
    if (depth > 12) throw new Error('Game view is too deep.');
    const values: GameValue[] =
      node.repeat === null ? [null] : asArray(evaluate(node.repeat, scope));
    if (values.length > 256) throw new Error('Game repetition exceeds its budget.');
    return values.map((item) => {
      if (++nodes > 1024) throw new Error('Game view exceeds its budget.');
      const local: GameRecord =
        node.repeat === null ? scope : { ...scope, [node.binding]: item };
      let props: GameRecord = Object.fromEntries(
        Object.entries(node.props).map(([key, expr]) => [key, evaluate(expr, local)]),
      );
      let action: RenderedGameAction | null = null;
      if (node.action) {
        const name = asString(evaluate(node.action.name, local));
        const transition = readMapEntry(program.transitions, name);
        const permission: GamePermission =
          node.action.permission ??
          (transition.permission === 'contributor' || transition.actor === 'contributor'
            ? 'contributor'
            : 'orator');
        action = {
          name,
          arguments: asRecord(evaluate(node.action.arguments, local)),
          confirmation: node.action.confirmation,
          permission,
        };
      }
      let children: RenderedGameNode[] = node.children.flatMap((child) =>
        render(child, local, depth + 1),
      );
      if (node.kind === 'svg' || node.kind === 'image') {
        const key = (['src', 'href', 'asset'] as const).find((propName) =>
          Object.hasOwn(props, propName),
        );
        if (key) {
          const raw = props[key];
          delete props[key];
          if (typeof raw === 'string' && raw) {
            const assetPath = raw.replace(/^game\//, '').split('/').filter(Boolean).join('/');
            const asset = readMapEntry(program.assets, assetPath);
            props = { ...asset.props, ...props, src: assetPath };
            children = [...asset.children, ...children];
          }
        }
      }
      return {
        kind: node.kind === 'image' ? 'svg' : node.kind,
        props,
        children,
        action,
      };
    });
  }

  return {
    apply(state: GameRecord, action: LocalGameActionInput): LocalGameSnapshot {
      try {
        resetBudget();
        const username = typeof action.username === 'string' ? action.username : '';
        const transition = readMapEntry(program.transitions, action.name);
        const scope: GameRecord = { ...context(state, username), action: action.arguments };
        if (!boolean(evaluate(transition.guard, scope))) {
          throw new InvalidGameInput('This action is not permitted in the current game state.');
        }
        const transitionBindings =
          'bindings' in transition && Array.isArray(transition.bindings)
            ? (transition.bindings as readonly GameBinding[])
            : [];
        bindings(transitionBindings, scope);
        const transitionResult =
          'result' in transition && typeof transition.result === 'string' ? transition.result : '';
        const candidate = asRecord(evaluate(transitionResult, scope));
        const next = context(candidate, username);
        validate(next);
        const view = render(program.view, next);
        // JSON serialization also removes prototypes and enforces a bounded local snapshot.
        const snapshot = JSON.stringify({ state: candidate, view });
        if (snapshot.length > 262144) throw new Error('Game preview exceeds its size budget.');
        return JSON.parse(snapshot) as LocalGameSnapshot;
      } catch (failure: unknown) {
        if (failure instanceof InvalidGameInput) throw failure;
        throw new Error(
          'Game action could not be completed; the action was undone. Try another action.',
          { cause: failure },
        );
      }
    },
  };
}
