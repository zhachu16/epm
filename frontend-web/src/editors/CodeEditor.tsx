import Editor from "@monaco-editor/react";
import { editorTheme } from "../lib/monaco";

/** A Monaco editor for whole files: JSON, Python or definitions. */
export function CodeEditor({
  value,
  language,
  onChange,
  height = "100%",
  readOnly = false,
  testId,
}: {
  value: string;
  language: string;
  onChange?: (value: string) => void;
  height?: string | number;
  readOnly?: boolean;
  testId?: string;
}) {
  return (
    <div className="code-editor" style={{ height }} data-testid={testId}>
      <Editor
        value={value}
        language={language}
        theme={editorTheme()}
        onChange={(v) => onChange?.(v ?? "")}
        options={{
          readOnly,
          minimap: { enabled: false },
          fontSize: 13,
          scrollBeyondLastLine: false,
          tabSize: language === "python" ? 4 : 2,
          automaticLayout: true,
          wordWrap: "on",
        }}
      />
    </div>
  );
}
