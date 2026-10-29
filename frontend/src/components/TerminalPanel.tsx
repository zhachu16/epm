import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import { useEffect, useRef } from "react";
import { socketUrl } from "../api/client";
import type { TerminalResult } from "../api/types";

const PROMPT = "\x1b[36mfm>\x1b[0m ";

/** Runs `fm` commands in the session (not a shell). History with ↑/↓; `clear` clears. */
export function TerminalPanel({ visible }: { visible: boolean }) {
  const host = useRef<HTMLDivElement>(null);
  const fit = useRef<FitAddon | null>(null);

  useEffect(() => {
    const dark = window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? true;
    const term = new Terminal({
      fontFamily: 'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
      fontSize: 12,
      cursorBlink: true,
      convertEol: true,
      theme: { background: "#0f1218", foreground: dark ? "#e3e7ee" : "#e3e7ee" },
    });
    const fitter = new FitAddon();
    fit.current = fitter;
    term.loadAddon(fitter);
    term.open(host.current!);
    fitter.fit();
    term.writeln("Run fm commands against this session, e.g. \x1b[1mshow equity_irr\x1b[0m, \x1b[1mtrace ebit\x1b[0m, \x1b[1mhistory\x1b[0m, \x1b[1m--help\x1b[0m.");

    let socket: WebSocket | null = null;
    let line = "";
    const history: string[] = [];
    let cursor = 0;
    let waiting = false;

    const prompt = () => term.write(PROMPT);
    const connect = () => {
      socket = new WebSocket(socketUrl("/api/terminal"));
      socket.onmessage = (message) => {
        const result = JSON.parse(message.data) as TerminalResult;
        if (result.stdout) term.write(result.stdout);
        if (result.stderr) term.write(`\x1b[31m${result.stderr}\x1b[0m`);
        waiting = false;
        prompt();
      };
      socket.onclose = () => {
        if (waiting) {
          term.writeln("\x1b[31m(disconnected)\x1b[0m");
          waiting = false;
          prompt();
        }
      };
    };
    connect();
    prompt();

    const replace = (text: string) => {
      term.write("\b \b".repeat(line.length));
      line = text;
      term.write(line);
    };

    const disposable = term.onData((data) => {
      if (waiting) return;
      if (data === "\r") {
        term.write("\r\n");
        const command = line.trim();
        line = "";
        if (!command) return prompt();
        history.push(command);
        cursor = history.length;
        if (command === "clear") {
          term.clear();
          return prompt();
        }
        if (!socket || socket.readyState !== WebSocket.OPEN) {
          connect();
          term.writeln("\x1b[31mnot connected; try again\x1b[0m");
          return prompt();
        }
        waiting = true;
        socket.send(JSON.stringify({ command }));
      } else if (data === "\x7f") {
        if (line.length) {
          line = line.slice(0, -1);
          term.write("\b \b");
        }
      } else if (data === "\x1b[A") {
        if (cursor > 0) replace(history[--cursor]);
      } else if (data === "\x1b[B") {
        if (cursor < history.length) {
          cursor++;
          replace(history[cursor] ?? "");
        }
      } else if (data === "\x03") {
        line = "";
        term.write("^C\r\n");
        prompt();
      } else if (data >= " " || data.length > 1) {
        const text = data.replace(/[\r\n]+/g, " ");
        if (!text.startsWith("\x1b")) {
          line += text;
          term.write(text);
        }
      }
    });

    const observer = new ResizeObserver(() => {
      try {
        fitter.fit();
      } catch {
        /* hidden */
      }
    });
    observer.observe(host.current!);
    return () => {
      observer.disconnect();
      disposable.dispose();
      socket?.close();
      term.dispose();
    };
  }, []);

  useEffect(() => {
    if (visible) requestAnimationFrame(() => fit.current?.fit());
  }, [visible]);

  return <div ref={host} className="terminal-host" data-testid="terminal" style={{ display: visible ? "block" : "none" }} />;
}
