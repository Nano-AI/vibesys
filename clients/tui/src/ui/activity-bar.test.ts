import {describe, expect, it} from 'bun:test';
import type {ActiveAgentExecution} from '../session-model.js';
import {activitySummary, THINKING_WORDS} from './activity-bar.js';

const STARTED_AT = '2026-08-25T12:00:00.000Z';
const STARTED_AT_MS = Date.parse(STARTED_AT);

function execution(
  activity: ActiveAgentExecution['activity'],
  executionId = 'execution-1',
): ActiveAgentExecution {
  return {
    executionId,
    agentKind: 'implementer',
    roundLabel: 'round-1-implementer',
    roundNumber: 1,
    stage: 'implementation',
    attempt: 1,
    assignment: 'Implement the queue',
    startedAt: STARTED_AT,
    activity,
  };
}

describe('activity summary', () => {
  it('cycles generic thinking words on a stable five-second cadence', () => {
    const active = execution({mode: 'thinking', summary: 'Thinking'});
    const first = activitySummary(active, STARTED_AT_MS);

    expect(first).not.toBe('Thinking');
    expect(activitySummary(active, STARTED_AT_MS + 4_999)).toBe(first);
    expect(activitySummary(active, STARTED_AT_MS + 5_000)).not.toBe(first);
    expect(activitySummary(active, STARTED_AT_MS + 5_000)).toBe(
      activitySummary(active, STARTED_AT_MS + 5_000),
    );

    const cycle = THINKING_WORDS.map((_, index) =>
      activitySummary(active, STARTED_AT_MS + index * 5_000),
    );
    expect(new Set(cycle).size).toBe(THINKING_WORDS.length);
    expect(activitySummary(active, STARTED_AT_MS + THINKING_WORDS.length * 5_000)).toBe(first);
  });

  it('uses a large pool of present-participle activity words', () => {
    expect(THINKING_WORDS.length).toBeGreaterThanOrEqual(40);
    expect(new Set(THINKING_WORDS).size).toBe(THINKING_WORDS.length);
    expect(THINKING_WORDS.every(word => /ing$/i.test(word))).toBe(true);
    expect(THINKING_WORDS).not.toContain('Thinking');
  });

  it('preserves specific semantic summaries and tool fallbacks', () => {
    const planning = execution({mode: 'thinking', summary: 'Planning'});
    const todo = execution({mode: 'thinking', summary: 'Reviewing queue invariants'});
    const tool = execution({mode: 'tool', summary: 'Running queue tests', tool: 'Bash'});
    const blankTool = execution({mode: 'tool', summary: '', tool: 'Bash'});

    expect(activitySummary(planning, STARTED_AT_MS + 10_000)).toBe('Planning');
    expect(activitySummary(todo, STARTED_AT_MS + 10_000)).toBe('Reviewing queue invariants');
    expect(activitySummary(tool, STARTED_AT_MS + 10_000)).toBe('Running queue tests');
    expect(activitySummary(blankTool, STARTED_AT_MS + 10_000)).toBe('Using Bash');
  });

  it('uses the same deterministic sequence for a resumed execution', () => {
    const first = execution({mode: 'thinking', summary: ''}, 'stable-execution');
    const resumed = execution({mode: 'thinking', summary: 'Thinking'}, 'stable-execution');
    const timestamps = [0, 5_000, 55_000, 235_000].map(offset => STARTED_AT_MS + offset);

    expect(timestamps.map(now => activitySummary(first, now))).toEqual(
      timestamps.map(now => activitySummary(resumed, now)),
    );
  });
});
