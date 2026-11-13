import fs from 'node:fs';
import path from 'node:path';
import type { GamePayload } from '../../frontend/canvas/game-renderer.js';
import type {
  GameRecord,
  LocalGameActionInput,
  RenderedGameNode,
} from '../../frontend/canvas/game-runtime.js';
import { REPO_ROOT } from './canvas_fixtures';

export interface RuntimeStepExpectation {
  readonly step: LocalGameActionInput;
  readonly state: GameRecord;
  readonly view: RenderedGameNode[];
}

export interface RuntimeGameCase {
  readonly payload: GamePayload;
  readonly steps: readonly RuntimeStepExpectation[];
  readonly draft_move_result?: GamePayload;
}

export interface DoodleStampItem {
  readonly type: 'stamp';
  readonly id: string;
  readonly name: string;
  readonly stamp_id: string;
  readonly managed_by?: string;
  readonly game_key?: string;
  readonly game_package?: string;
  readonly draggable?: boolean;
  readonly canvas_asset_id?: string;
  readonly canvas_asset_revision?: string;
  readonly url: string;
  readonly x: number;
  readonly y: number;
  readonly rotation: number;
  readonly size: number;
  readonly canvas_size: number;
  readonly canvas_width: number;
  readonly canvas_height: number;
  readonly canvas_page_width: number;
  readonly canvas_page_height: number;
}

export interface DoodleSnapshotPayload {
  readonly type: string;
  readonly page_id: string;
  readonly enabled: boolean;
  readonly batches: readonly unknown[];
  readonly annotations: readonly unknown[];
  readonly stamps: readonly DoodleStampItem[];
}

export interface GameSnapshots {
  readonly runtime_counter: RuntimeGameCase;
  readonly runtime_checkers: RuntimeGameCase;
  readonly fail_guard: GamePayload;
  readonly fail_invariant: GamePayload;
  readonly fail_evaluation: GamePayload;
  readonly example_checkers_0: GamePayload;
  readonly example_checkers_1: GamePayload;
  readonly example_checkers_2: GamePayload;
  readonly example_checkers_3: GamePayload;
  readonly example_checkers_4: GamePayload;
  readonly checkers_near_win_0: GamePayload;
  readonly checkers_near_win_1: GamePayload;
  readonly checkers_near_win_2: GamePayload;
  readonly checkers_near_win_stamps: DoodleSnapshotPayload;
  readonly form_0: GamePayload;
  readonly form_1: GamePayload;
  readonly panel_0: GamePayload;
  readonly panel_1: GamePayload;
  readonly counter_badge_0: GamePayload;
  readonly counter_badge_1: GamePayload;
  readonly counter_badge_err: GamePayload;
  readonly counter_badge_2: GamePayload;
}

export interface SubmittedGameAction {
  readonly name: string;
  readonly arguments: Record<string, unknown>;
  readonly revision: number;
  readonly draft?: readonly LocalGameActionInput[];
  readonly canvas_revision?: number;
}

let cachedSnapshots: GameSnapshots | null = null;
let cachedCanvasJs: string | null = null;

export function loadGameSnapshots(): GameSnapshots {
  if (!cachedSnapshots) {
    const filePath = path.join(REPO_ROOT, 'testing', 'testdata', 'game_snapshots.json');
    cachedSnapshots = JSON.parse(fs.readFileSync(filePath, 'utf-8')) as GameSnapshots;
  }
  return cachedSnapshots;
}

export function readCanvasJsSource(): string {
  if (cachedCanvasJs === null) {
    cachedCanvasJs = fs.readFileSync(path.join(REPO_ROOT, 'static', 'js', 'canvas.js'), 'utf-8');
  }
  return cachedCanvasJs;
}

export function sliceCanvasJs(startMarker: string, endMarker: string): string {
  const source = readCanvasJsSource();
  const start = source.indexOf(startMarker);
  if (start < 0) {
    throw new Error(`Could not find start marker in static/js/canvas.js: ${startMarker}`);
  }
  const end = source.indexOf(endMarker, start);
  if (end <= start) {
    throw new Error(`Could not find end marker in static/js/canvas.js: ${endMarker}`);
  }
  return source.slice(start, end);
}

export function parseSubmittedAction(postData: string | null): SubmittedGameAction {
  return JSON.parse(postData || '{}') as SubmittedGameAction;
}
