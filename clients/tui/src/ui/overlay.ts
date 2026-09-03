import {BoxRenderable, type CliRenderer, TextRenderable} from '@opentui/core';
import {focusedPane, type RightPane, type SessionState} from '../session-model.js';
import {applyPaneFocus} from './focus.js';
import type {Theme} from './theme.js';

type OverlayKind = NonNullable<SessionState['overlay']>['kind'];

const TITLE: Record<OverlayKind, string> = {
  detail: 'Command',
  help: 'Help',
  error: 'Error',
};

function borderFor(theme: Theme, kind: OverlayKind): string {
  if (kind === 'help') return theme.success;
  if (kind === 'error') return theme.error;
  return theme.info;
}

export class OverlayView {
  readonly output: BoxRenderable;
  #theme: Theme;
  #renderedKind: OverlayKind | null = null;
  #renderedContent = '';
  #renderedPane: RightPane | null = null;

  constructor(
    private readonly renderer: CliRenderer,
    theme: Theme,
  ) {
    this.#theme = theme;
    this.output = new BoxRenderable(renderer, {
      id: 'overlay',
      width: '70%',
      height: '60%',
      position: 'absolute',
      left: '15%',
      top: '18%',
      flexDirection: 'column',
      paddingLeft: 1,
      paddingRight: 1,
      border: true,
      borderStyle: 'rounded',
      borderColor: theme.info,
      backgroundColor: theme.elevatedSurface,
      // Above the chat modal (20), below the theme picker (30): a command ack
      // submitted from the modal chat has to be visible over it.
      zIndex: 25,
    });
  }

  applyTheme(theme: Theme): void {
    this.#theme = theme;
    this.output.backgroundColor = theme.elevatedSurface;
    this.output.borderColor = borderFor(theme, this.#renderedKind ?? 'detail');
    this.#renderedKind = null;
    this.#renderedContent = '';
    this.#renderedPane = null;
  }

  /**
   * `pane` is the visualization the terminal is too narrow to split, drawn here
   * because there is no column to put it in. It is the same surface as
   * `RightPaneView` and takes the same keys, so it wears that pane's title and
   * asks `focusedPane` the same question rather than appearing as a `Command`
   * box with none of the focus treatment on the one thing taking keystrokes.
   */
  render(state: SessionState, pane: RightPane | null = null): void {
    if (pane !== null) {
      this.#renderPane(state, pane);
      return;
    }
    this.#renderedPane = null;
    const overlay = state.overlay;
    if (overlay === null) {
      this.output.visible = false;
      return;
    }
    this.output.visible = true;
    if (this.#renderedKind === overlay.kind && this.#renderedContent === overlay.content) return;
    this.#renderedKind = overlay.kind;
    this.#renderedContent = overlay.content;
    this.output.borderColor = borderFor(this.#theme, overlay.kind);
    this.output.title = ` ${TITLE[overlay.kind]} `;
    this.#clear();
    this.#line(
      overlay.content,
      overlay.kind === 'error' ? this.#theme.conversation.failure.content : this.#theme.textPrimary,
    );
    this.#line('Esc to close', this.#theme.textSubtle);
  }

  #renderPane(state: SessionState, pane: RightPane): void {
    this.output.visible = true;
    // Outside the cache below: focus moves without the content changing.
    applyPaneFocus(this.output, this.#theme, pane.title, focusedPane(state) === 'performance');
    if (pane === this.#renderedPane) return;
    this.#renderedPane = pane;
    // The next ordinary overlay repaints its own title and border rather than
    // inheriting the pane's.
    this.#renderedKind = null;
    this.#renderedContent = '';
    this.#clear();
    if (pane.error !== null) this.#line(pane.error, this.#theme.conversation.failure.content);
    else if (pane.pending && pane.content === '') {
      this.#line('Loading...', this.#theme.textSubtle);
    } else this.#line(pane.content, this.#theme.textPrimary);
    this.#line('Esc to close', this.#theme.textSubtle);
  }

  #line(content: string, fg: string): void {
    this.output.add(new TextRenderable(this.renderer, {content, fg, width: '100%'}));
  }

  #clear(): void {
    for (const child of [...this.output.getChildren()]) {
      this.output.remove(child);
      child.destroyRecursively();
    }
  }
}
