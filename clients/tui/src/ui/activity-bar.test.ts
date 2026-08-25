import {describe, expect, it} from 'bun:test';
import type {ActiveAgentExecution} from '../session-model.js';
import {activitySummary} from './activity-bar.js';

const STARTED_AT = '2026-08-25T12:00:00.000Z';
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
  it('uses Working for generic thinking activity', () => {
    expect(activitySummary(execution({mode: 'thinking', summary: ''}))).toBe('Working');
    expect(activitySummary(execution({mode: 'thinking', summary: 'Thinking'}))).toBe('Working');
    expect(activitySummary(execution({mode: 'thinking', summary: 'Thinking...'}))).toBe('Working');
    expect(activitySummary(execution({mode: 'thinking', summary: 'Thinking…'}))).toBe('Working');
  });

  it('preserves specific semantic summaries and tool fallbacks', () => {
    const planning = execution({mode: 'thinking', summary: 'Planning'});
    const todo = execution({mode: 'thinking', summary: 'Reviewing queue invariants'});
    const tool = execution({mode: 'tool', summary: 'Running queue tests', tool: 'Bash'});
    const blankTool = execution({mode: 'tool', summary: '', tool: 'Bash'});

    expect(activitySummary(planning)).toBe('Planning');
    expect(activitySummary(todo)).toBe('Reviewing queue invariants');
    expect(activitySummary(tool)).toBe('Running queue tests');
    expect(activitySummary(blankTool)).toBe('Using Bash');
  });
});
