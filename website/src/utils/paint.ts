/*
 * paint.ts — colour the landing page's terminal panels at build time. Each panel holds text as the server or a shell
 * prints it; these helpers escape it, colour each line by its shape and wrap it in a block, so the page ships HTML and
 * no script. A line holding only `…` marks where a panel leaves part of an answer or a file out.
 */

const esc = (text: string): string => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const span = (cls: string, html: string): string => `<span class="${cls}">${html}</span>`;

/** One line as a block; `hang` (in characters) is where a line too long for the panel goes on when it wraps. */
const line = (html: string, cls = '', hang?: number): string => {
  const style = hang === undefined ? '' : ` style="padding-left:${hang}ch;text-indent:-${hang}ch"`;
  return `<span class="ln${cls ? ' ' + cls : ''}"${style}>${html || ' '}</span>`;
};

/** A line's own indentation, and two more: a wrapped line goes on under its own text. */
const hangOf = (raw: string): number => (raw.match(/^ */)?.[0].length ?? 0) + 2;

const isCut = (raw: string): boolean => raw.trim() === '…';

type Value = string | number | boolean | null;

/**
 * A tool call as an agent makes it: `→ describe_metric(metric="revenue")`. A `null` argument is shown as `…` — left
 * out of the panel, where the answer below repeats it.
 */
export function call(tool: string, args: Record<string, Value> = {}): string {
  const shown = Object.entries(args)
    .map(([key, value]) => {
      if (value === null) return `${key}=${span('t-cut', '…')}`;
      if (typeof value === 'string') return `${key}=${span('t-str', esc(JSON.stringify(value)))}`;
      return `${key}=${span('t-num', String(value))}`;
    })
    .join(', ');
  return line(`${span('t-arrow', '→')} ${span('t-fn', tool)}(${shown})`, 't-call');
}

/** A note under a call, folded: `└ what it answered`, in the panel's own words. */
export function fold(text: string): string {
  return line(`${span('t-dim', '└')} ${span('t-fold', esc(text))}`, '', 2);
}

const JSON_TOKEN = /("(?:[^"\\]|\\.)*")(\s*:)?|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|\b(true|false|null)\b|([{}[\],])|(…)/g;

/** A JSON answer, pretty-printed: keys, strings, numbers, literals and punctuation. */
export function json(text: string): string {
  return text
    .split('\n')
    .map((raw) => {
      if (isCut(raw)) return line(span('t-cut', esc(raw)), '', hangOf(raw));
      let html = '';
      let last = 0;
      for (const m of raw.matchAll(JSON_TOKEN)) {
        html += esc(raw.slice(last, m.index));
        if (m[1]) html += m[2] ? span('t-key', esc(m[1])) + span('t-punct', esc(m[2])) : span('t-str', esc(m[1]));
        else if (m[3]) html += span('t-num', m[3]);
        else if (m[4]) html += span('t-lit', m[4]);
        else if (m[5]) html += span('t-punct', m[5]);
        else if (m[6]) html += span('t-cut', m[6]);
        last = (m.index ?? 0) + m[0].length;
      }
      html += esc(raw.slice(last));
      return line(html, '', hangOf(raw));
    })
    .join('');
}

/** A tool's error, as the client gets it: a refusal by the guard names its code. */
export function refusal(text: string): string {
  const m = text.match(/^(Error executing tool \w+: )(Refused by the guard) \((\w+)\)(: )([\s\S]*)$/);
  if (!m) return line(esc(text), 't-refused');
  return line(
    `${span('t-dim', esc(m[1]))}${span('t-refused', m[2])} ${span('t-code', `(${m[3]})`)}${esc(m[4])}${span('t-reason', esc(m[5]))}`,
  );
}

/** A YAML file: keys, strings and comments. */
export function yaml(text: string): string {
  return text
    .split('\n')
    .map((raw) => {
      if (isCut(raw)) return line(span('t-cut', esc(raw)), '', hangOf(raw));
      const at = raw.search(/(^|\s)#/);
      const code = at === -1 ? raw : raw.slice(0, at);
      const comment = at === -1 ? '' : raw.slice(at);
      // strings first: the spans the other two add carry quotes of their own
      let h = esc(code)
        .replace(/"[^"]*"/g, (s) => span('t-str', s))
        .replace(/^(\s*(?:- )?)([A-Za-z_][\w.-]*)(:)/, (_, lead, key, colon) => `${lead}${span('t-key', key)}${colon}`)
        .replace(/(:\s+)(\d+|true|false)(\s*)$/, (_, sep, n, tail) => `${sep}${span('t-num', n)}${tail}`);
      if (comment) h += span('t-comment', esc(comment));
      return line(h, '', hangOf(raw) + (/^\s*- /.test(raw) ? 2 : 0));
    })
    .join('');
}

/** A line a command printed: what passed in green, what failed in rose, the likely fix in green. */
function printed(raw: string): string {
  let h = esc(raw);
  if (/^(The configuration .* is fine:|Wrote |.* is current\.$)/.test(raw)) return line(span('t-ok', h));
  if (/^semantic-warehouse-mcp: .* problems?:$/.test(raw)) return line(span('t-refused', h));
  if (/^\s*\{.*\}\s*$/.test(raw)) return json(raw);
  h = h
    .replace(/ → /g, span('t-dim', ' → '))
    .replace(/'([^']+)'( is not one of its values)/, (_, value, rest) => `${span('t-mod', `'${value}'`)}${rest}`)
    .replace(/(did you mean )'([^']+)'\?/, (_, lead, value) => `${lead}${span('t-ok', `'${value}'`)}?`);
  return line(h, 't-out', hangOf(raw) + (/^\s*- /.test(raw) ? 2 : 0));
}

/** A shell session: `$ ` commands (a line ending in `\` goes on in the next), `# ` comments, and what they print. */
export function shell(text: string): string {
  let goesOn = false;
  return text
    .split('\n')
    .map((raw) => {
      if (goesOn) {
        goesOn = raw.endsWith('\\');
        return line(esc(raw), 't-cont');
      }
      if (raw.startsWith('$ ')) {
        const rest = raw.slice(2);
        const at = rest.search(/\s#\s/);
        const cmd = at === -1 ? rest : rest.slice(0, at);
        const comment = at === -1 ? '' : rest.slice(at);
        goesOn = cmd.trimEnd().endsWith('\\');
        return line(`${span('t-prompt', '$')} ${esc(cmd)}${comment ? span('t-comment', esc(comment)) : ''}`, 't-cmd');
      }
      if (raw.startsWith('#')) return line(esc(raw), 't-comment');
      return printed(raw);
    })
    .join('');
}
