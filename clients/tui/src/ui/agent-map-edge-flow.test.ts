import {afterEach, describe, expect, it, spyOn} from 'bun:test';
import type {Renderable} from '@opentui/core';
import {rgbToHex} from '@opentui/core';
import {createTestRenderer, type TestRendererSetup} from '@opentui/core/testing';
import type {AgentPhase} from '@vibesys/core-state';
import type {SessionController} from '../session-controller.js';
import {initialSessionState, type SessionState} from '../session-model.js';
import {AgentMapView} from './agent-map.js';
import {resolveTheme} from './theme.js';

// Kept apart from agent-map-spinner.test.ts for the same reason that suite is
// split from agent-map.test.ts: unrelated changes to either should not
// textually conflict with these edge-flow-specific tests.

/** Mirrors the identity check in agent-map-spinner.test.ts: a tick must mutate
 * the existing renderable tree in place, never rebuild it. */
function descendants(node: Renderable): Renderable[] {
  return [node, ...node.getChildren().flatMap(descendants)];
}

/** One row of the frame, as cells in column order with their foreground colour. */
interface Cell {
  ch: string;
  fg: string;
}

function spanRows(setup: TestRendererSetup): Cell[][] {
  return setup.captureSpans().lines.map(line => {
    const cells: Cell[] = [];
    for (const span of line.spans) {
      for (const ch of span.text) cells.push({ch, fg: rgbToHex(span.fg).toLowerCase()});
    }
    return cells;
  });
}

/** Never fires in a render-only test; onMouseUp is never simulated. */
const controller = {
  focusRound: () => {},
  selectAgent: () => {},
} as unknown as SessionController;

function stateWith(phases: AgentPhase[]): SessionState {
  const base = initialSessionState();
  return {...base, core: {...base.core, phases}, selectedAgentKind: null};
}

function phase(kind: string, status: AgentPhase['status']): AgentPhase {
  return {kind, status, roundNumber: null, roundLabel: null};
}

describe('agent graph edge dataflow dot', () => {
  const cleanup: Array<() => void> = [];

  afterEach(() => {
    for (const destroy of cleanup.splice(0).reverse()) destroy();
  });

  /**
   * Spies on `setInterval` before rendering so the view's one spinner/flow
   * timer (agent-map.ts `#syncSpinnerTimer`, shared by both concerns per the
   * design) can be advanced deterministically by calling its captured
   * callback directly, rather than racing real wall-clock timers the way
   * agent-map-spinner.test.ts does for its looser "the frame differs"
   * assertion. Here we need an exact single tick, so a real `setTimeout`
   * wait is the wrong tool.
   */
  async function setUp(
    phases: AgentPhase[],
    width = 100,
    override = 70,
  ): Promise<{view: AgentMapView; testRenderer: TestRendererSetup; tick: () => void}> {
    const setIntervalSpy = spyOn(globalThis, 'setInterval');
    const testRenderer = await createTestRenderer({width, height: 24});
    const view = new AgentMapView(testRenderer.renderer, controller, resolveTheme(null));
    testRenderer.renderer.root.add(view.output);
    cleanup.push(() => {
      view.destroy();
      view.output.destroyRecursively();
      testRenderer.renderer.destroy();
      setIntervalSpy.mockRestore();
    });
    view.render(stateWith(phases), override);
    await testRenderer.renderOnce();
    const call = setIntervalSpy.mock.calls[0];
    const tick = call?.[0] as (() => void) | undefined;
    return {
      view,
      testRenderer,
      tick: () => {
        expect(tick).toBeDefined();
        tick?.();
      },
    };
  }

  it('draws a dot on the completed-to-active inbound edge, and moves it one cell per tick', async () => {
    const {testRenderer, tick} = await setUp([
      phase('orchestrator', 'completed'),
      phase('implementer', 'active'),
    ]);
    const beforeLines = testRenderer.captureCharFrame().split('\n');
    const rowIndex = beforeLines.findIndex(line => line.includes('•'));
    expect(rowIndex).toBeGreaterThanOrEqual(0);
    const dotColumn = (beforeLines[rowIndex] ?? '').indexOf('•');

    tick();
    await testRenderer.renderOnce();
    const afterLine = testRenderer.captureCharFrame().split('\n')[rowIndex] ?? '';

    // The old cell reverted to the dim line, and the dot is now one cell along.
    expect(afterLine[dotColumn]).not.toBe('•');
    expect(afterLine[dotColumn + 1]).toBe('•');
  });

  it('mutates the edge run renderables in place: same instances before and after a tick', async () => {
    const {view, testRenderer, tick} = await setUp([
      phase('orchestrator', 'completed'),
      phase('implementer', 'active'),
    ]);
    const before = descendants(view.output);

    tick();
    await testRenderer.renderOnce();
    const after = descendants(view.output);

    expect(after.length).toBe(before.length);
    expect(after.length).toBeGreaterThan(5);
    for (const [index, node] of before.entries()) {
      expect(after[index]).toBe(node);
    }
  });

  it("never draws a dot on the active node's own outbound edge, across several ticks", async () => {
    const {testRenderer, tick} = await setUp(
      [
        phase('orchestrator', 'completed'),
        phase('implementer', 'active'),
        phase('judge', 'pending'),
      ],
      120,
      90,
    );
    // Only one edge in this round can qualify (orchestrator -> implementer,
    // completed -> active): if the outbound implementer -> judge edge ever
    // carried a dot too, this count would climb past one.
    for (let step = 0; step < 4; step += 1) {
      const frame = testRenderer.captureCharFrame();
      expect((frame.match(/•/g) ?? []).length).toBe(1);
      tick();
      await testRenderer.renderOnce();
    }
  });

  it('draws no dot and runs no timer while no node is active', async () => {
    const setIntervalSpy = spyOn(globalThis, 'setInterval');
    const testRenderer = await createTestRenderer({width: 100, height: 24});
    const view = new AgentMapView(testRenderer.renderer, controller, resolveTheme(null));
    testRenderer.renderer.root.add(view.output);
    cleanup.push(() => {
      view.destroy();
      view.output.destroyRecursively();
      testRenderer.renderer.destroy();
      setIntervalSpy.mockRestore();
    });

    view.render(
      stateWith([phase('orchestrator', 'completed'), phase('implementer', 'completed')]),
      70,
    );
    await testRenderer.renderOnce();

    expect(testRenderer.captureCharFrame()).not.toContain('•');
    expect(setIntervalSpy).not.toHaveBeenCalled();
  });

  it('colours the dot with the live edge accent, and the rest of the edge with the idle border colour', async () => {
    const theme = resolveTheme(null);
    const testRenderer = await createTestRenderer({width: 100, height: 24});
    const view = new AgentMapView(testRenderer.renderer, controller, theme);
    testRenderer.renderer.root.add(view.output);
    cleanup.push(() => {
      view.destroy();
      view.output.destroyRecursively();
      testRenderer.renderer.destroy();
    });
    view.render(
      stateWith([phase('orchestrator', 'completed'), phase('implementer', 'active')]),
      70,
    );
    await testRenderer.renderOnce();

    const rows = spanRows(testRenderer);
    const rowIndex = rows.findIndex(row => row.some(cell => cell.ch === '•'));
    expect(rowIndex).toBeGreaterThanOrEqual(0);
    const row = rows[rowIndex] ?? [];
    const dotColumn = row.findIndex(cell => cell.ch === '•');

    expect(row[dotColumn]?.fg).toBe(theme.accent.toLowerCase());
    // A neighbouring line cell (never a corner or the arrowhead) carries the
    // dim idle colour instead.
    const neighbourColumn = row[dotColumn + 1]?.ch === '─' ? dotColumn + 1 : dotColumn - 1;
    expect(row[neighbourColumn]?.ch).toBe('─');
    expect(row[neighbourColumn]?.fg).toBe(theme.borderStrong.toLowerCase());
  });
});
