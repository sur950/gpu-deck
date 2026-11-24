/**
 * Connect screen: saved servers (rail) + detail / add / edit panel.
 *
 * Plain DOM, no framework. Every string from a server (branding, health) or
 * the user goes through text nodes, never innerHTML.
 */

import { errorMessage, type Backend } from './lib/ipc';
import {
  checkLabel,
  checkTone,
  hasErrors,
  relativeTime,
  sortServers,
  suggestApiUrl,
  toneLabel,
  toneOf,
  validateForm,
  type FormErrors,
  type Prefill,
  type ProbeResult,
  type Server,
  type ServerInput,
  type ServersView,
  type Tone,
} from './lib/model';

type Child = Node | string | null | undefined | false;
type Props = Record<string, unknown>;

export function h<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: Props = {},
  ...children: Child[]
): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key.startsWith('on') && typeof value === 'function') {
      el.addEventListener(key.slice(2).toLowerCase(), value as EventListener);
    } else if (key === 'class') {
      el.className = String(value);
    } else if (key === 'style' && typeof value === 'object') {
      for (const [prop, v] of Object.entries(value as Record<string, string>))
        el.style.setProperty(prop, v);
    } else if (value === true) {
      el.setAttribute(key, '');
    } else {
      el.setAttribute(key, String(value));
    }
  }
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    el.append(
      typeof child === 'string' ? document.createTextNode(child) : child,
    );
  }
  return el;
}

const led = (tone: Tone, label?: string) =>
  h('span', {
    class: 'led',
    'data-tone': tone,
    role: label ? 'img' : undefined,
    'aria-label': label,
  });

type Mode = 'detail' | 'add' | 'edit';

interface Draft {
  id?: string;
  name: string;
  url: string;
  apiUrl: string;
  showAdvanced: boolean;
}

const emptyDraft = (): Draft => ({
  name: '',
  url: '',
  apiUrl: '',
  showAdvanced: false,
});

export class ConnectApp {
  private view: ServersView | null = null;
  private selectedId: string | null = null;
  private mode: Mode = 'detail';
  private probes = new Map<string, ProbeResult | 'checking'>();
  private draft: Draft = emptyDraft();
  private draftProbe: ProbeResult | 'checking' | null = null;
  private draftErrors: FormErrors = {};
  private touched = false;
  private notice: string | null = null;
  private pendingPath: string | undefined;
  private confirmRemove = false;
  private busy = false;
  private toastTimer: ReturnType<typeof setTimeout> | null = null;
  /** Scroll the probe card into view after the next render (fresh result). */
  private revealProbe = false;

  constructor(
    private readonly root: HTMLElement,
    private readonly backend: Backend,
    private readonly now: () => number = Date.now,
  ) {}

  async start(): Promise<void> {
    this.backend.onPrefill((p) => this.applyPrefill(p));
    this.backend.onServersChanged(() => void this.reload());
    document.addEventListener('keydown', (e) => this.onKey(e));
    await this.reload();
    const prefill = await this.backend.prefill().catch(() => null);
    if (prefill) this.applyPrefill(prefill);
    this.probeAll();
    // First run / prefilled link: start typing the address right away.
    if (this.mode !== 'detail') document.getElementById('server-url')?.focus();
  }

  get servers(): Server[] {
    return sortServers(this.view?.servers ?? []);
  }

  private get selected(): Server | undefined {
    return this.servers.find((s) => s.id === this.selectedId);
  }

  async reload(): Promise<void> {
    try {
      this.view = await this.backend.list();
    } catch (e) {
      this.toast(errorMessage(e));
      this.view = {
        servers: [],
        lastServerId: null,
        currentServerId: null,
        appVersion: '',
        platform: '',
      };
    }
    const ids = new Set(this.servers.map((s) => s.id));
    if (!this.selectedId || !ids.has(this.selectedId)) {
      this.selectedId =
        this.view.lastServerId && ids.has(this.view.lastServerId)
          ? this.view.lastServerId
          : (this.servers[0]?.id ?? null);
    }
    if (!this.servers.length && this.mode === 'detail') this.startAdd();
    this.render();
  }

  private probeAll(): void {
    for (const s of this.servers) void this.probeServer(s);
  }

  async probeServer(server: Server): Promise<void> {
    this.probes.set(server.id, 'checking');
    this.render();
    try {
      this.probes.set(
        server.id,
        await this.backend.probe(server.url, server.apiUrl),
      );
    } catch (e) {
      this.probes.delete(server.id);
      this.toast(errorMessage(e));
    }
    this.render();
  }

  applyPrefill(p: Prefill): void {
    this.mode = 'add';
    this.draft = { ...emptyDraft(), name: p.name ?? '', url: p.url ?? '' };
    this.draftProbe = null;
    this.touched = Boolean(p.url);
    this.draftErrors = this.touched
      ? validateForm(this.draftInput(), this.servers).errors
      : {};
    this.notice = p.reason;
    this.pendingPath = p.path;
    this.render();
  }

  startAdd(): void {
    this.mode = 'add';
    this.draft = emptyDraft();
    this.draftProbe = null;
    this.draftErrors = {};
    this.touched = false;
    this.notice = null;
    this.pendingPath = undefined;
    this.confirmRemove = false;
    this.render();
  }

  startEdit(server: Server): void {
    this.mode = 'edit';
    this.draft = {
      id: server.id,
      name: server.name,
      url: server.url,
      apiUrl: server.apiUrl ?? '',
      showAdvanced: Boolean(server.apiUrl),
    };
    const p = this.probes.get(server.id);
    this.draftProbe = p && p !== 'checking' ? p : null;
    this.draftErrors = {};
    this.touched = true;
    this.notice = null;
    this.confirmRemove = false;
    this.render();
  }

  select(id: string): void {
    this.selectedId = id;
    this.mode = 'detail';
    this.confirmRemove = false;
    this.notice = null;
    this.render();
  }

  private draftInput(): ServerInput {
    return {
      id: this.draft.id,
      name: this.draft.name,
      url: this.draft.url,
      apiUrl: this.draft.apiUrl.trim() || undefined,
    };
  }

  async checkDraft(): Promise<void> {
    const check = validateForm(this.draftInput(), this.servers);
    this.touched = true;
    this.draftErrors = { url: check.errors.url, apiUrl: check.errors.apiUrl };
    if (check.errors.url || check.errors.apiUrl) {
      this.render();
      return;
    }
    this.draftProbe = 'checking';
    this.render();
    try {
      this.draftProbe = await this.backend.probe(
        this.draft.url,
        this.draft.apiUrl.trim() || undefined,
      );
      this.revealProbe = true;
      if (!this.draft.name.trim() && this.draftProbe.branding && check.origin) {
        // Suggest a name from the host; the user can change it.
        this.draft.name = new URL(check.origin).hostname.split('.')[0] ?? '';
      }
    } catch (e) {
      this.draftProbe = null;
      this.draftErrors = { ...this.draftErrors, url: errorMessage(e) };
    }
    this.render();
  }

  async saveDraft(connect: boolean): Promise<void> {
    const check = validateForm(this.draftInput(), this.servers);
    this.touched = true;
    this.draftErrors = check.errors;
    if (hasErrors(check.errors)) {
      this.render();
      return;
    }
    this.busy = true;
    this.render();
    try {
      const saved = await this.backend.save(this.draftInput());
      if (this.draftProbe && this.draftProbe !== 'checking')
        this.probes.set(saved.id, this.draftProbe);
      this.selectedId = saved.id;
      this.mode = 'detail';
      this.notice = null;
      await this.reload();
      if (connect) await this.connect(saved.id);
      else if (!this.probes.has(saved.id)) void this.probeServer(saved);
    } catch (e) {
      this.toast(errorMessage(e));
    } finally {
      this.busy = false;
      this.render();
    }
  }

  async remove(server: Server): Promise<void> {
    try {
      await this.backend.remove(server.id);
      this.probes.delete(server.id);
      this.selectedId = null;
      this.confirmRemove = false;
      this.mode = 'detail';
      await this.reload();
    } catch (e) {
      this.toast(errorMessage(e));
    }
  }

  async connect(id: string): Promise<void> {
    this.busy = true;
    this.render();
    try {
      const path = this.pendingPath;
      this.pendingPath = undefined;
      await this.backend.connect(id, path);
    } catch (e) {
      this.toast(errorMessage(e));
    } finally {
      this.busy = false;
      this.render();
    }
  }

  private onKey(e: KeyboardEvent): void {
    const mod = e.metaKey || e.ctrlKey;
    const typing = e.target instanceof HTMLInputElement;
    if (mod && e.key.toLowerCase() === 'n') {
      e.preventDefault();
      this.startAdd();
    } else if (e.key === 'Escape') {
      if (this.mode !== 'detail' && this.servers.length) {
        this.mode = 'detail';
        this.notice = null;
        this.render();
      } else if (this.view?.currentServerId) {
        void this.backend.back();
      }
    } else if (
      e.key === 'Enter' &&
      !typing &&
      this.mode === 'detail' &&
      this.selected &&
      !this.busy
    ) {
      void this.connect(this.selected.id);
    } else if (
      !typing &&
      this.mode === 'detail' &&
      (e.key === 'ArrowDown' || e.key === 'ArrowUp')
    ) {
      const list = this.servers;
      const idx = list.findIndex((s) => s.id === this.selectedId);
      const next =
        list[
          Math.max(
            0,
            Math.min(list.length - 1, idx + (e.key === 'ArrowDown' ? 1 : -1)),
          )
        ];
      if (next) {
        e.preventDefault();
        this.select(next.id);
        (
          this.root.querySelector(
            `[data-id="${CSS.escape(next.id)}"]`,
          ) as HTMLElement | null
        )?.focus();
      }
    }
  }

  toast(message: string): void {
    this.root.querySelector('.toast')?.remove();
    const el = h('div', { class: 'toast', role: 'alert' }, message);
    this.root.append(el);
    if (this.toastTimer) clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(() => el.remove(), 6000);
  }

  // ── Rendering ─────────────────────────────────────────────────────────

  render(): void {
    const toast = this.root.querySelector('.toast');
    const active = document.activeElement as HTMLElement | null;
    const focus = active?.id
      ? {
          id: active.id,
          start:
            active instanceof HTMLInputElement ? active.selectionStart : null,
          end: active instanceof HTMLInputElement ? active.selectionEnd : null,
        }
      : null;
    const scrollTop = this.root.querySelector('.main')?.scrollTop ?? 0;
    this.root.replaceChildren(this.renderRail(), this.renderMain());
    if (toast) this.root.append(toast);
    const main = this.root.querySelector('.main');
    if (main) main.scrollTop = scrollTop;
    if (focus) {
      const el = document.getElementById(focus.id);
      el?.focus({ preventScroll: true });
      if (el instanceof HTMLInputElement) {
        el.setSelectionRange(
          focus.start ?? el.value.length,
          focus.end ?? el.value.length,
        );
      }
    }
    if (this.revealProbe) {
      this.revealProbe = false;
      this.root
        .querySelector('[data-testid="probe"]')
        ?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
    }
  }

  private toneFor(id: string): Tone {
    const p = this.probes.get(id);
    return p === 'checking' ? 'checking' : toneOf(p);
  }

  private renderRail(): HTMLElement {
    const servers = this.servers;
    const current = this.view?.currentServerId;
    return h(
      'aside',
      { class: 'rail' },
      h('div', { class: 'drag', 'data-tauri-drag-region': true }),
      h(
        'div',
        { class: 'lockup', 'aria-label': 'GPU Deck, GPU control plane' },
        h('img', { src: '/gpudeck-mark.svg', alt: '' }),
        h(
          'span',
          { class: 'lockup-text' },
          h('span', { class: 'lockup-name' }, 'GPU Deck'),
          h('span', { class: 'lockup-byline' }, 'GPU control plane'),
        ),
      ),
      h(
        'div',
        { class: 'rail-head' },
        h('span', { class: 'eyebrow' }, 'Control planes'),
        h(
          'span',
          { class: 'eyebrow' },
          String(servers.length).padStart(2, '0'),
        ),
      ),
      h(
        'ul',
        { class: 'server-list', 'aria-label': 'Saved servers' },
        ...servers.map((s, i) => {
          const tone = this.toneFor(s.id);
          return h(
            'li',
            { class: 'reveal', style: { '--i': String(i) } },
            h(
              'button',
              {
                class: 'server',
                type: 'button',
                'data-id': s.id,
                'aria-current':
                  this.mode === 'detail' && s.id === this.selectedId
                    ? 'true'
                    : 'false',
                onClick: () => this.select(s.id),
                onDblclick: () => void this.connect(s.id),
              },
              h(
                'span',
                { class: 'unit' },
                `U${String(i + 1).padStart(2, '0')}`,
              ),
              led(tone, toneLabel(tone)),
              h(
                'span',
                { style: { 'min-width': '0' } },
                h(
                  'span',
                  { class: 'server-name' },
                  s.name,
                  s.id === current
                    ? h('span', { class: 'live' }, '· open')
                    : null,
                ),
                h(
                  'span',
                  { class: 'server-url', style: { display: 'block' } },
                  s.url.replace(/^https:\/\//, ''),
                ),
                h(
                  'span',
                  { class: 'server-meta', style: { display: 'block' } },
                  relativeTime(s.lastUsedAt, this.now()),
                ),
              ),
            ),
          );
        }),
      ),
      h(
        'div',
        { class: 'rail-foot' },
        h(
          'button',
          { class: 'add', type: 'button', onClick: () => this.startAdd() },
          h('span', {}, '+ Add server'),
          h(
            'span',
            { class: 'kbd' },
            this.view?.platform === 'macos' ? '⌘N' : 'Ctrl+N',
          ),
        ),
        h(
          'div',
          { class: 'version' },
          `Desktop ${this.view?.appVersion ?? ''}`.trim(),
        ),
      ),
    );
  }

  private renderMain(): HTMLElement {
    let panel: HTMLElement;
    if (this.mode === 'add' || this.mode === 'edit') panel = this.renderForm();
    else if (this.selected) panel = this.renderDetail(this.selected);
    else panel = this.renderForm();
    return h(
      'main',
      { class: 'main' },
      h('div', { class: 'drag', 'data-tauri-drag-region': true }),
      panel,
    );
  }

  private renderDetail(s: Server): HTMLElement {
    const probe = this.probes.get(s.id);
    const isOpen = this.view?.currentServerId === s.id;
    let i = 0;
    const r = () => ({ class: 'reveal', style: { '--i': String(i++) } });
    return h(
      'section',
      { class: 'panel', 'aria-label': s.name },
      h(
        'div',
        r(),
        h(
          'span',
          { class: 'eyebrow' },
          isOpen ? 'Connected control plane' : 'Control plane',
        ),
      ),
      h('h1', { ...r(), class: 'title reveal' }, s.name),
      h(
        'div',
        { ...r(), class: 'address reveal' },
        h('span', {}, s.url),
        s.insecure
          ? h(
              'span',
              { class: 'chip', 'data-tone': 'warn' },
              'http · unencrypted',
            )
          : null,
        s.apiUrl
          ? h(
              'span',
              { class: 'chip' },
              `api ${s.apiUrl.replace(/^https?:\/\//, '')}`,
            )
          : null,
      ),
      s.insecure
        ? h(
            'div',
            { ...r(), class: 'callout reveal', 'data-tone': 'warn' },
            'Plain http: your session cookie and everything you do travels unencrypted. Use this only on your own machine or a trusted private network.',
          )
        : null,
      h(
        'div',
        { ...r(), class: 'actions reveal' },
        h(
          'button',
          {
            id: 'btn-connect',
            class: 'btn btn-primary',
            type: 'button',
            disabled: this.busy,
            onClick: () => void this.connect(s.id),
          },
          isOpen ? 'Return to server' : 'Connect',
          h('span', { class: 'kbd' }, '⏎'),
        ),
        h(
          'button',
          {
            id: 'btn-check',
            class: 'btn',
            type: 'button',
            disabled: probe === 'checking',
            onClick: () => void this.probeServer(s),
          },
          probe === 'checking' ? 'Checking…' : 'Check again',
        ),
        h(
          'button',
          {
            id: 'btn-edit',
            class: 'btn',
            type: 'button',
            onClick: () => this.startEdit(s),
          },
          'Edit',
        ),
        this.confirmRemove
          ? h(
              'span',
              { class: 'actions', style: { margin: '0' } },
              h(
                'button',
                {
                  class: 'btn btn-danger-solid',
                  type: 'button',
                  onClick: () => void this.remove(s),
                },
                `Remove ${s.name}`,
              ),
              h(
                'button',
                {
                  class: 'btn',
                  type: 'button',
                  onClick: () => {
                    this.confirmRemove = false;
                    this.render();
                  },
                },
                'Keep',
              ),
            )
          : h(
              'button',
              {
                class: 'btn btn-danger',
                type: 'button',
                onClick: () => {
                  this.confirmRemove = true;
                  this.render();
                },
              },
              'Remove',
            ),
      ),
      h('div', r(), this.renderProbe(probe, s.apiUrl)),
    );
  }

  renderProbe(
    probe: ProbeResult | 'checking' | null | undefined,
    apiUrl?: string,
  ): HTMLElement {
    if (probe === 'checking') {
      return h(
        'div',
        { class: 'card' },
        h(
          'div',
          { class: 'card-head' },
          led('checking'),
          h('strong', {}, 'Checking server…'),
        ),
      );
    }
    if (!probe) {
      return h(
        'div',
        { class: 'card' },
        h(
          'div',
          { class: 'card-head' },
          led('unknown'),
          h('strong', {}, 'Not checked yet'),
        ),
      );
    }
    const tone = toneOf(probe);
    const head = h(
      'div',
      { class: 'card-head' },
      led(tone),
      h('strong', {}, probe.isGpuDeck ? toneLabel(tone) : 'Not reachable'),
      h('span', { class: 'muted mono' }, `${probe.latencyMs} ms`),
    );
    if (!probe.isGpuDeck) {
      const suggestion = !apiUrl ? suggestApiUrl(probe.origin) : null;
      return h(
        'div',
        { class: 'card', 'data-testid': 'probe' },
        head,
        h(
          'div',
          { class: 'problem' },
          h('p', {}, probe.error ?? 'No GPU Deck admin API answered.'),
          h(
            'p',
            { class: 'muted' },
            'The app checks ',
            h('span', { class: 'mono' }, `${probe.apiBase}/api/v1/branding`),
            ' and ',
            h('span', { class: 'mono' }, '/api/v1/health'),
            '. When the admin API is not served from the same address as the web UI, set its address under “Admin API URL”.',
          ),
          suggestion
            ? h(
                'p',
                { class: 'muted' },
                `Local dev stack? Its admin API is usually ${suggestion}.`,
              )
            : null,
        ),
      );
    }
    const b = probe.branding;
    const health = probe.health;
    return h(
      'div',
      { class: 'card', 'data-testid': 'probe' },
      head,
      h(
        'dl',
        { class: 'facts' },
        h(
          'div',
          { class: 'fact' },
          h('dt', { class: 'eyebrow' }, 'Application'),
          h(
            'dd',
            {},
            b
              ? h('span', {
                  class: 'swatch',
                  style: { background: b.brandColor },
                })
              : null,
            b?.appName ?? 'GPU Deck',
            h('small', {}, b?.appByline ?? ''),
          ),
        ),
        h(
          'div',
          { class: 'fact' },
          h('dt', { class: 'eyebrow' }, 'Version'),
          h(
            'dd',
            { class: 'mono' },
            health?.version ?? 'unknown',
            h('small', {}, `health: ${health?.status ?? 'n/a'}`),
          ),
        ),
        h(
          'div',
          { class: 'fact' },
          h('dt', { class: 'eyebrow' }, 'Admin API'),
          h(
            'dd',
            { class: 'mono', title: probe.apiBase },
            probe.apiBase.replace(/^https?:\/\//, ''),
            h('small', {}, '/api/v1'),
          ),
        ),
      ),
      health?.checks.length
        ? h(
            'div',
            { class: 'checks', 'aria-label': 'Dependency checks' },
            ...health.checks.map((c) =>
              h(
                'span',
                { class: 'check', title: c.status },
                led(checkTone(c.status)),
                checkLabel(c.name),
              ),
            ),
          )
        : null,
    );
  }

  private field(
    id: string,
    label: string,
    value: string,
    opts: {
      placeholder?: string;
      mono?: boolean;
      hint?: string;
      error?: string;
      onInput: (v: string) => void;
    },
  ): HTMLElement {
    const input = h('input', {
      id,
      value,
      placeholder: opts.placeholder,
      class: opts.mono ? 'mono' : undefined,
      autocomplete: 'off',
      autocapitalize: 'off',
      spellcheck: 'false',
      'aria-invalid': opts.error ? 'true' : undefined,
      'aria-describedby': `${id}-msg`,
      onInput: (e: Event) => opts.onInput((e.target as HTMLInputElement).value),
      onKeydown: (e: KeyboardEvent) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          void this.saveDraft(e.metaKey || e.ctrlKey);
        }
      },
    });
    input.value = value;
    return h(
      'div',
      { class: 'field' },
      h('label', { for: id }, label),
      input,
      opts.error
        ? h('div', { class: 'error', id: `${id}-msg` }, opts.error)
        : opts.hint
          ? h('div', { class: 'hint', id: `${id}-msg` }, opts.hint)
          : null,
    );
  }

  private onDraftInput(key: 'name' | 'url' | 'apiUrl', value: string): void {
    this.draft[key] = value;
    if (key !== 'name') this.draftProbe = null;
    if (this.touched) {
      this.draftErrors = validateForm(this.draftInput(), this.servers).errors;
      this.render();
    } else if (key === 'url' && this.draftProbe === null) {
      // Re-render only to update the http warning; keep it cheap.
      this.render();
    }
  }

  private renderForm(): HTMLElement {
    const editing = this.mode === 'edit';
    const first = !this.servers.length;
    const check = validateForm(this.draftInput(), this.servers);
    const insecure = check.insecure && !check.errors.url;
    const suggestion = suggestApiUrl(check.origin);
    let i = 0;
    const r = () => ({ class: 'reveal', style: { '--i': String(i++) } });
    return h(
      'section',
      { class: 'panel', 'aria-label': editing ? 'Edit server' : 'Add server' },
      h(
        'div',
        r(),
        h(
          'span',
          { class: 'eyebrow' },
          editing
            ? 'Edit control plane'
            : first
              ? 'Welcome'
              : 'New control plane',
        ),
      ),
      h(
        'h1',
        { ...r(), class: 'title reveal' },
        editing
          ? this.draft.name || 'Edit server'
          : first
            ? 'Connect to your GPU fleet.'
            : 'Add a control plane',
      ),
      h(
        'p',
        { ...r(), class: 'subtitle reveal' },
        'GPU Deck Desktop opens the web UI of a GPU Deck control plane in its own window, with native notifications and a tray status. You sign in inside the app; nothing you type there is stored on this computer.',
      ),
      this.notice
        ? h(
            'div',
            {
              ...r(),
              class: 'callout reveal',
              'data-tone': 'info',
              role: 'status',
            },
            this.notice,
          )
        : null,
      h(
        'div',
        { ...r(), class: 'form reveal' },
        this.field('server-url', 'Server URL', this.draft.url, {
          placeholder: 'https://gpudeck.example.com',
          mono: true,
          hint: 'The address you open the GPU Deck web UI at. https:// is assumed; http:// works only for local and private addresses.',
          error: this.touched ? this.draftErrors.url : undefined,
          onInput: (v) => this.onDraftInput('url', v),
        }),
        this.field('server-name', 'Name', this.draft.name, {
          placeholder: 'Production',
          hint: 'Shown in the server list, the tray and deep links (gpudeck://open?server=<name>).',
          error: this.touched ? this.draftErrors.name : undefined,
          onInput: (v) => this.onDraftInput('name', v),
        }),
        (() => {
          const details = h(
            'details',
            {
              class: 'advanced',
              open: this.draft.showAdvanced || Boolean(this.draftErrors.apiUrl),
            },
            h('summary', {}, 'Admin API URL (optional)'),
            this.field('server-api', 'Admin API URL', this.draft.apiUrl, {
              placeholder: suggestion ?? 'Same as the server URL',
              mono: true,
              hint: suggestion
                ? `Only when the admin API has its own address. The local dev stack serves it at ${suggestion}.`
                : 'Only when the admin API has its own address (the web UI and /api usually share one).',
              error: this.touched ? this.draftErrors.apiUrl : undefined,
              onInput: (v) => this.onDraftInput('apiUrl', v),
            }),
          );
          details.addEventListener('toggle', () => {
            this.draft.showAdvanced = details.open;
          });
          return details;
        })(),
      ),
      insecure
        ? h(
            'div',
            {
              ...r(),
              class: 'callout reveal',
              'data-tone': 'warn',
              role: 'status',
            },
            'This is a plain http address. Traffic, including your session cookie, is not encrypted. Fine for localhost or a trusted private network, never across the internet.',
          )
        : null,
      h(
        'div',
        { ...r(), class: 'actions reveal' },
        h(
          'button',
          {
            id: 'btn-save-connect',
            class: 'btn btn-primary',
            type: 'button',
            disabled: this.busy,
            onClick: () => void this.saveDraft(true),
          },
          editing ? 'Save and connect' : 'Add and connect',
        ),
        h(
          'button',
          {
            id: 'btn-check-draft',
            class: 'btn',
            type: 'button',
            disabled: this.draftProbe === 'checking',
            onClick: () => void this.checkDraft(),
          },
          this.draftProbe === 'checking' ? 'Checking…' : 'Check server',
        ),
        h(
          'button',
          {
            id: 'btn-save',
            class: 'btn',
            type: 'button',
            disabled: this.busy,
            onClick: () => void this.saveDraft(false),
          },
          'Save',
        ),
        this.servers.length
          ? h(
              'button',
              {
                class: 'btn',
                type: 'button',
                onClick: () => {
                  this.mode = 'detail';
                  this.notice = null;
                  this.render();
                },
              },
              'Cancel',
            )
          : null,
      ),
      this.draftProbe
        ? h('div', r(), this.renderProbe(this.draftProbe, this.draft.apiUrl))
        : null,
    );
  }
}
