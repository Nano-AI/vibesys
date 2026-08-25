import {type CliRenderer, TextRenderable} from '@opentui/core';
import type {ActiveAgentExecution, SessionState} from '../session-model.js';
import {visibleRoundNumber} from '../session-model.js';
import type {Theme} from './theme.js';

const SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];
const SPINNER_INTERVAL_MS = 120;
const THINKING_WORD_INTERVAL_MS = 5_000;

export const THINKING_WORDS = [
  'Pondering',
  'Reasoning',
  'Exploring',
  'Inspecting',
  'Analyzing',
  'Searching',
  'Reading',
  'Reviewing',
  'Checking',
  'Tracing',
  'Mapping',
  'Comparing',
  'Testing',
  'Building',
  'Editing',
  'Refining',
  'Validating',
  'Measuring',
  'Profiling',
  'Debugging',
  'Investigating',
  'Calculating',
  'Modeling',
  'Simulating',
  'Organizing',
  'Synthesizing',
  'Drafting',
  'Evaluating',
  'Verifying',
  'Considering',
  'Preparing',
  'Coordinating',
  'Sequencing',
  'Optimizing',
  'Experimenting',
  'Discovering',
  'Resolving',
  'Learning',
  'Parsing',
  'Compiling',
  'Linking',
  'Sampling',
  'Forecasting',
  'Rechecking',
  'Rethinking',
  'Iterating',
  'Focusing',
] as const;
export type ActivityBarScope = 'global' | 'conversation';

/** Stable, selection-aware status line for backend-authoritative execution activity. */
export class ActivityBarView {
  readonly output: TextRenderable;
  #executions: ActiveAgentExecution[] = [];
  #frame = 0;
  #timer: ReturnType<typeof setInterval> | null = null;

  constructor(renderer: CliRenderer, theme: Theme, id = 'activity-bar') {
    this.output = new TextRenderable(renderer, {
      id,
      height: 1,
      width: '100%',
      wrapMode: 'none',
      truncate: true,
      fg: theme.textMuted,
      content: '',
      visible: false,
    });
  }

  render(state: SessionState, scope: ActivityBarScope, visible = true): void {
    const all = Object.values(state.activeExecutions);
    const roundNumber = visibleRoundNumber(state);
    this.#executions = visible
      ? scope === 'global'
        ? all
        : all.filter(
            execution =>
              (roundNumber === null || execution.roundNumber === roundNumber) &&
              (state.selectedAgentKind === null || execution.agentKind === state.selectedAgentKind),
          )
      : [];
    this.output.visible = this.#executions.length > 0;
    this.#refresh();
    this.#syncTimer();
  }

  applyTheme(theme: Theme): void {
    this.output.fg = theme.textMuted;
  }

  destroy(): void {
    if (this.#timer !== null) clearInterval(this.#timer);
    this.#timer = null;
  }

  #syncTimer(): void {
    if (this.#executions.length === 0) {
      if (this.#timer !== null) clearInterval(this.#timer);
      this.#timer = null;
      return;
    }
    if (this.#timer !== null) return;
    this.#timer = setInterval(() => {
      this.#frame = (this.#frame + 1) % SPINNER_FRAMES.length;
      this.#refresh();
    }, SPINNER_INTERVAL_MS);
  }

  #refresh(): void {
    const spinner = SPINNER_FRAMES[this.#frame] ?? SPINNER_FRAMES[0];
    const nowMs = Date.now();
    if (this.#executions.length === 1) {
      const execution = this.#executions[0];
      if (execution === undefined) return;
      this.output.content = `${spinner} ${roleLabel(execution.agentKind)} · ${activitySummary(execution, nowMs)} · ${elapsed(execution.startedAt, nowMs)}`;
      return;
    }
    const summaries = this.#executions
      .slice(0, 3)
      .map(execution => `${roleLabel(execution.agentKind)}: ${activitySummary(execution, nowMs)}`)
      .join(' · ');
    const remainder = this.#executions.length > 3 ? ` · +${this.#executions.length - 3} more` : '';
    this.output.content = `${spinner} ${this.#executions.length} agents active · ${summaries}${remainder}`;
  }
}

export function activitySummary(execution: ActiveAgentExecution, nowMs = Date.now()): string {
  const summary = execution.activity.summary.trim();
  if (
    execution.activity.mode === 'thinking' &&
    (summary === '' || /^thinking(?:\.\.\.|\u2026)?$/i.test(summary))
  ) {
    return thinkingWord(execution, nowMs);
  }
  if (summary !== '') return summary;
  if (execution.activity.mode === 'tool' && execution.activity.tool) {
    return `Using ${execution.activity.tool}`;
  }
  return modeLabel(execution.activity.mode);
}

function thinkingWord(execution: ActiveAgentExecution, nowMs: number): string {
  const hash = stableHash(execution.executionId);
  const offset = hash % THINKING_WORDS.length;
  let stride = 1 + ((hash >>> 8) % (THINKING_WORDS.length - 1));
  while (greatestCommonDivisor(stride, THINKING_WORDS.length) !== 1) stride += 1;
  if (stride >= THINKING_WORDS.length) stride = 1;

  const startedAtMs = Date.parse(execution.startedAt);
  const elapsedMs = Number.isFinite(startedAtMs) ? Math.max(0, nowMs - startedAtMs) : 0;
  const tick = Math.floor(elapsedMs / THINKING_WORD_INTERVAL_MS);
  return THINKING_WORDS[(offset + tick * stride) % THINKING_WORDS.length] ?? 'Pondering';
}

function stableHash(value: string): number {
  let hash = 2_166_136_261;
  for (const character of value) {
    hash ^= character.codePointAt(0) ?? 0;
    hash = Math.imul(hash, 16_777_619);
  }
  return hash >>> 0;
}

function greatestCommonDivisor(left: number, right: number): number {
  let a = left;
  let b = right;
  while (b !== 0) {
    const remainder = a % b;
    a = b;
    b = remainder;
  }
  return a;
}

function roleLabel(role: string): string {
  if (role === '') return 'Agent';
  return role.charAt(0).toUpperCase() + role.slice(1).replaceAll('_', ' ');
}

function modeLabel(mode: ActiveAgentExecution['activity']['mode']): string {
  const labels: Record<typeof mode, string> = {
    thinking: 'Thinking',
    responding: 'Responding',
    tool: 'Using a tool',
    waiting: 'Waiting',
  };
  return labels[mode];
}

function elapsed(startedAt: string, nowMs: number): string {
  const milliseconds = nowMs - Date.parse(startedAt);
  const seconds = Number.isFinite(milliseconds) ? Math.max(0, Math.floor(milliseconds / 1000)) : 0;
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${seconds % 60}s`;
}
