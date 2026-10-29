import { loader } from "@monaco-editor/react";
// The editor core plus the two languages the UI uses, instead of every language Monaco ships.
import * as monaco from "monaco-editor/esm/vs/editor/edcore.main";
import "monaco-editor/esm/vs/basic-languages/python/python.contribution";
import "monaco-editor/esm/vs/language/json/monaco.contribution";
import EditorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import JsonWorker from "monaco-editor/esm/vs/language/json/json.worker?worker";
import type { AppState } from "../api/types";
import { formatNumber, headline } from "./format";

// Bundle Monaco locally (no CDN), since the UI runs offline next to the model.
self.MonacoEnvironment = {
  getWorker(_id: string, label: string) {
    return label === "json" ? new JsonWorker() : new EditorWorker();
  },
};
loader.config({ monaco });

export const FORMULA = "epm-formula";
let context: AppState | null = null;

/** Keep completions and hovers in step with the current model. */
export function setFormulaContext(state: AppState) {
  context = state;
}

export function editorTheme(): string {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "vs-dark" : "vs";
}

monaco.languages.register({ id: FORMULA });
monaco.languages.setMonarchTokensProvider(FORMULA, {
  tokenizer: {
    root: [
      [/\d+(\.\d+)?([eE][-+]?\d+)?/, "number"],
      [/[A-Za-z_]\w*(?=\s*\()/, "keyword"],
      [/\b(if|else|and|or|not)\b/, "keyword"],
      [/[A-Za-z_]\w*/, "identifier"],
      [/'[^']*'|"[^"]*"/, "string"],
      [/[-+*/%<>=!&|]+/, "operator"],
    ],
  },
});
monaco.languages.setLanguageConfiguration(FORMULA, {
  brackets: [["(", ")"]],
  autoClosingPairs: [
    { open: "(", close: ")" },
    { open: "'", close: "'" },
  ],
});

monaco.languages.registerCompletionItemProvider(FORMULA, {
  triggerCharacters: ["(", ",", " "],
  provideCompletionItems(model, position) {
    const word = model.getWordUntilPosition(position);
    const range = new monaco.Range(position.lineNumber, word.startColumn, position.lineNumber, word.endColumn);
    if (!context) return { suggestions: [] };
    const K = monaco.languages.CompletionItemKind;
    const metrics = Object.entries(context.metrics).map(([name, m]) => ({
      label: name,
      kind: m.kind === "value" || m.kind === "input" ? K.Constant : K.Variable,
      insertText: name,
      detail: [m.unit, m.axis ? `axis ${m.axis}` : "scalar"].filter(Boolean).join(" · "),
      documentation: m.formula ?? `${m.kind}: ${formatNumber(headline(m.value).value, m.unit)}`,
      range,
    }));
    const builtins = context.builtins.map((b) => ({
      label: b.name,
      kind: K.Function,
      insertText: `${b.name}(`,
      detail: b.signature,
      documentation: b.doc,
      range,
    }));
    const functions = context.functions.map((f) => ({
      label: f.name,
      kind: K.Method,
      insertText: `${f.name}(`,
      detail: `${f.signature} → ${f.returns}`,
      documentation: "custom function from functions.py",
      range,
    }));
    return { suggestions: [...metrics, ...builtins, ...functions] };
  },
});

monaco.languages.registerHoverProvider(FORMULA, {
  provideHover(model, position) {
    const word = model.getWordAtPosition(position);
    if (!word || !context) return null;
    const metric = context.metrics[word.word];
    if (metric) {
      const { label, value } = headline(metric.value);
      return {
        contents: [
          { value: `**${word.word}** ${metric.unit ? `(${metric.unit})` : ""}` },
          { value: metric.formula ? "```\n" + metric.formula + "\n```" : `_${metric.kind}_` },
          { value: `value${label ? ` (${label})` : ""}: ${formatNumber(value, metric.unit)}` },
        ],
      };
    }
    const builtin = context.builtins.find((b) => b.name === word.word);
    if (builtin) return { contents: [{ value: `\`${builtin.signature}\`` }, { value: builtin.doc }] };
    const fn = context.functions.find((f) => f.name === word.word);
    if (fn) return { contents: [{ value: `\`${fn.signature}\` → ${fn.returns}` }, { value: "custom function" }] };
    return null;
  },
});

export { monaco };
