// A data-only interpreter for the server's compiled CEL subset. Never executes package JS.
const own = (value, key) => value !== null && Object.hasOwn(value, key);
const equal = (left, right) => {
    if (left === right) return true;
    if (!left || !right || typeof left !== 'object' || typeof right !== 'object') return false;
    const keys = Object.keys(left);
    return keys.length === Object.keys(right).length && keys.every(key => own(right, key) && equal(left[key], right[key]));
};
const size = value => typeof value === 'string' ? Array.from(value).length : Object.keys(value).length;
const boolean = value => {
    if (typeof value !== 'boolean') throw new Error('Expected a boolean.');
    return value;
};
class InvalidGameInput extends Error {}

export function createLocalGame(program) {
    let remaining;
    let deadline;
    let nodes;
    function resetBudget() { remaining = 500000; deadline = performance.now() + 1000; nodes = 0; }
    function charge() {
        if (--remaining < 0 || (remaining % 256 === 0 && performance.now() > deadline)) {
            throw new Error('Game preview exceeded its work budget.');
        }
    }
    function read(value, key) {
        if (!own(value, key)) throw new Error('Unknown game field.');
        return value[key];
    }
    function evaluate(source, scope) { return visit(read(program.expressions, source), scope); }
    function visit(tree, scope) {
        charge();
        const result = interpret(tree, scope);
        if (typeof result === 'number' && !Number.isSafeInteger(result)) throw new Error('Game preview exceeds integer precision.');
        return result;
    }
    function interpret(tree, scope) {
        const [kind, ...args] = tree;
        const value = index => visit(args[index], scope);
        switch (kind) {
        case 'literal': return args[0];
        case 'ident': return read(scope, args[0]);
        case 'expr': return boolean(value(0)) ? value(1) : value(2);
        case 'conditionaland': return boolean(value(0)) && boolean(value(1));
        case 'conditionalor': return boolean(value(0)) || boolean(value(1));
        case 'unary': return args[0][0] === 'unary_not' ? !boolean(value(1)) : -value(1);
        case 'member_dot': return read(value(0), args[1]);
        case 'member_index': return read(value(0), value(1));
        case 'list_lit': return args.length ? visit(args[0], scope) : [];
        case 'exprlist': return args.map(arg => visit(arg, scope));
        case 'map_lit': {
            const entries = args.length ? args[0].slice(1) : [];
            const result = Object.create(null);
            for (let i = 0; i < entries.length; i += 2) result[visit(entries[i], scope)] = visit(entries[i + 1], scope);
            return result;
        }
        case 'ident_arg': {
            const name = args[0];
            const values = args.length > 1 ? visit(args[1], scope) : [];
            if (name === 'size') return size(values[0]);
            if (name === 'string') return String(values[0]);
            if (name === 'int') {
                if (typeof values[0] === 'string' && !/^[+-]?\d+$/.test(values[0])) throw new Error('Invalid integer.');
                const number = Math.trunc(Number(values[0]));
                if (!Number.isSafeInteger(number)) throw new Error('Invalid integer.');
                return number;
            }
            if (name === 'bool') {
                if (values[0] === true || values[0] === 'true') return true;
                if (values[0] === false || values[0] === 'false') return false;
                throw new Error('Invalid boolean.');
            }
            const fn = read(program.functions, name);
            if (values.length !== fn.parameters.length) throw new Error('Invalid function arguments.');
            const bindings = Object.fromEntries(fn.parameters.map((param, index) => [param, values[index]]));
            return evaluate(fn.expr, { ...bindings, constants: program.constants });
        }
        case 'member_dot_arg': {
            const collection = value(0);
            const method = args[1];
            if (method === 'size') return size(collection);
            if (!Array.isArray(collection)) throw new Error('Expected a list.');
            const parameters = args[2].slice(1);
            const variable = parameters[0][1];
            const callback = item => visit(parameters[1], { ...scope, [variable]: item });
            if (method === 'map') return collection.map(callback);
            if (method === 'filter') return collection.filter(item => boolean(callback(item)));
            if (method === 'exists') return collection.some(item => boolean(callback(item)));
            if (method === 'all') return collection.every(item => boolean(callback(item)));
            throw new Error('Unsupported game method.');
        }
        case 'relation': case 'addition': case 'multiplication': {
            const [operator, leftTree] = args[0];
            const left = visit(leftTree, scope);
            const right = value(1);
            switch (operator) {
            case 'relation_eq': return equal(left, right);
            case 'relation_ne': return !equal(left, right);
            case 'relation_lt': return left < right;
            case 'relation_le': return left <= right;
            case 'relation_gt': return left > right;
            case 'relation_ge': return left >= right;
            case 'relation_in': return Array.isArray(right) ? right.some(item => equal(left, item)) : own(right, left);
            case 'addition_add': return Array.isArray(left) ? left.concat(right) : left + right;
            case 'addition_sub': return left - right;
            case 'multiplication_mul': return left * right;
            case 'multiplication_div':
                if (!right) throw new Error('Division by zero.');
                return Math.trunc(left / right);
            case 'multiplication_mod':
                if (!right) throw new Error('Division by zero.');
                return left % right;
            default: throw new Error('Unsupported game operator.');
            }
        }
        default: throw new Error('Unsupported game expression.');
        }
    }
    function bindings(definitions, scope) {
        for (const binding of definitions) {
            if (own(scope, binding.name)) throw new Error('Game binding shadows a name.');
            scope[binding.name] = evaluate(binding.expr, scope);
        }
    }
    function context(state) {
        const scope = { state, initial: program.initial, constants: program.constants };
        bindings(program.derived, scope);
        return scope;
    }
    function validate(scope) {
        if (!program.invariants.every(expr => boolean(evaluate(expr, scope)))) throw new Error('Game invariant failed.');
    }
    function render(node, scope, depth = 0) {
        if (depth > 12) throw new Error('Game view is too deep.');
        const values = node.repeat === null ? [null] : evaluate(node.repeat, scope);
        if (values.length > 256) throw new Error('Game repetition exceeds its budget.');
        return values.map(item => {
            if (++nodes > 1024) throw new Error('Game view exceeds its budget.');
            const local = node.repeat === null ? scope : { ...scope, [node.binding]: item };
            let props = Object.fromEntries(Object.entries(node.props).map(([key, expr]) => [key, evaluate(expr, local)]));
            let action = null;
            if (node.action) {
                const name = evaluate(node.action.name, local);
                const transition = read(program.transitions, name);
                action = { name, arguments: evaluate(node.action.arguments, local), confirmation: node.action.confirmation,
                    permission: node.action.permission ?? (transition.permission === 'contributor' || transition.actor === 'contributor' ? 'contributor' : 'orator') };
            }
            let children = node.children.flatMap(child => render(child, local, depth + 1));
            if (node.kind === 'svg' || node.kind === 'image') {
                const key = ['src', 'href', 'asset'].find(name => own(props, name));
                if (key) {
                    const raw = props[key];
                    delete props[key];
                    if (raw) {
                        const path = raw.replace(/^game\//, '').split('/').filter(Boolean).join('/');
                        const asset = read(program.assets, path);
                        props = { ...asset.props, ...props, src: path };
                        children = [...asset.children, ...children];
                    }
                }
            }
            return { kind: node.kind === 'image' ? 'svg' : node.kind, props, children, action };
        });
    }
    return {
        apply(state, action) {
            try {
                resetBudget();
                const transition = read(program.transitions, action.name);
                const scope = { ...context(state), action: action.arguments };
                if (!boolean(evaluate(transition.guard, scope))) throw new InvalidGameInput('This action is not permitted in the current game state.');
                bindings(transition.bindings, scope);
                const candidate = evaluate(transition.result, scope);
                const next = context(candidate);
                validate(next);
                const view = render(program.view, next);
                // JSON serialization also removes prototypes and enforces a bounded local snapshot.
                const snapshot = JSON.stringify({ state: candidate, view });
                if (snapshot.length > 262144) throw new Error('Game preview exceeds its size budget.');
                return JSON.parse(snapshot);
            } catch (failure) {
                if (failure instanceof InvalidGameInput) throw failure;
                throw new Error('Game action could not be completed; the action was undone. Try another action.', { cause: failure });
            }
        },
    };
}
