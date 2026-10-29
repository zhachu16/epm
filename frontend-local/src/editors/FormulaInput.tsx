import Editor, { type OnMount } from "@monaco-editor/react";
import { useRef } from "react";
import { editorTheme, FORMULA, monaco } from "../lib/monaco";

/** A formula field with completion of metrics, built-ins and custom functions. */
export function FormulaInput({
  value,
  onChange,
  onSubmit,
  testId,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit?: () => void;
  testId?: string;
}) {
  const submit = useRef(onSubmit);
  submit.current = onSubmit;
  const mount: OnMount = (editor) => {
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () => submit.current?.());
  };
  return (
    <div className="formula-box" data-testid={testId}>
      <Editor
        height={72}
        language={FORMULA}
        value={value}
        theme={editorTheme()}
        onMount={mount}
        onChange={(v) => onChange(v ?? "")}
        options={{
          minimap: { enabled: false },
          lineNumbers: "off",
          glyphMargin: false,
          folding: false,
          lineDecorationsWidth: 6,
          scrollBeyondLastLine: false,
          wordWrap: "on",
          fontSize: 13,
          renderLineHighlight: "none",
          overviewRulerLanes: 0,
          scrollbar: { vertical: "auto", horizontal: "hidden" },
          automaticLayout: true,
          suggest: { showWords: false },
        }}
      />
    </div>
  );
}
