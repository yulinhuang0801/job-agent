"use strict";
// Code editor for the Skills check: Monaco (the editor inside VS Code) loaded from cdnjs,
// with a plain-textarea fallback that still auto-closes brackets and keeps indentation,
// for when the CDN can't be reached (offline, or blocked networks).

const CodeEditor = (() => {
  const BASE = "https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.52.2/min/vs";
  let loading = null;

  function loadMonaco() {
    if (loading) return loading;
    loading = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("timeout")), 10000);
      // Monaco runs its helpers in web workers; load them from the CDN too.
      window.MonacoEnvironment = {
        getWorkerUrl: () => `data:text/javascript;charset=utf-8,${encodeURIComponent(
          `self.MonacoEnvironment={baseUrl:'${BASE.replace(/vs$/, "")}'};importScripts('${BASE}/base/worker/workerMain.js');`)}`,
      };
      const s = document.createElement("script");
      s.src = `${BASE}/loader.js`;
      s.onerror = () => { clearTimeout(timer); reject(new Error("load failed")); };
      s.onload = () => {
        window.require.config({ paths: { vs: BASE } });
        window.require(["vs/editor/editor.main"], () => { clearTimeout(timer); resolve(window.monaco); }, reject);
      };
      document.head.appendChild(s);
    });
    return loading;
  }

  const dark = () => window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;

  // ---- Fallback: textarea with bracket pairing, Tab = 4 spaces, Enter keeps indentation ----
  const PAIRS = { "(": ")", "[": "]", "{": "}", '"': '"', "'": "'" };

  function fallback(container, value, onChange) {
    const t = document.createElement("textarea");
    t.className = "code";
    t.rows = 10;
    t.spellcheck = false;
    t.value = value || "";
    t.addEventListener("keydown", (e) => {
      const { selectionStart: a, selectionEnd: b, value: v } = t;
      const put = (text, caret) => {
        t.setRangeText(text, a, b, "end");
        if (caret !== undefined) t.setSelectionRange(a + caret, a + caret);
        onChange && onChange(t.value);
      };
      if (e.key === "Tab") {
        e.preventDefault();
        put("    ");
      } else if (e.key === "Enter") {
        e.preventDefault();
        const line = v.slice(v.lastIndexOf("\n", a - 1) + 1, a);
        let indent = line.match(/^\s*/)[0];
        if (/[:{[(]\s*$/.test(line)) indent += "    ";
        put("\n" + indent);
      } else if (PAIRS[e.key] && a === b) {
        e.preventDefault();
        put(e.key + PAIRS[e.key], 1);
      } else if (Object.values(PAIRS).includes(e.key) && v[a] === e.key && a === b) {
        e.preventDefault();
        t.setSelectionRange(a + 1, a + 1); // type over the auto-inserted closer
      } else if (e.key === "Backspace" && a === b && PAIRS[v[a - 1]] === v[a]) {
        e.preventDefault();
        t.setRangeText("", a - 1, a + 1, "end");
        onChange && onChange(t.value);
      }
    });
    t.addEventListener("input", () => onChange && onChange(t.value));
    container.replaceChildren(t);
    return { getValue: () => t.value, setValue: (v) => { t.value = v; }, setLanguage: () => {}, dispose: () => {}, kind: "basic" };
  }

  // isCurrent() lets the caller cancel an editor whose page section was redrawn while Monaco loaded.
  async function create(container, { value = "", language = "python", onChange, isCurrent = () => true } = {}) {
    container.classList.add("code-editor");
    const fb = fallback(container, value, onChange); // usable immediately while Monaco loads
    try {
      const monaco = await loadMonaco();
      if (!isCurrent()) return fb;
      const current = fb.getValue();
      container.replaceChildren();
      const ed = monaco.editor.create(container, {
        value: current, language, theme: dark() ? "vs-dark" : "vs",
        automaticLayout: true, minimap: { enabled: false }, fontSize: 14, tabSize: 4,
        scrollBeyondLastLine: false, wordWrap: "on", lineNumbers: "on",
        autoClosingBrackets: "always", autoClosingQuotes: "always", autoIndent: "full",
        // No autocomplete: it's a skills test, like most coding-interview editors.
        quickSuggestions: false, suggestOnTriggerCharacters: false, wordBasedSuggestions: "off",
        parameterHints: { enabled: false }, renderLineHighlight: "line", padding: { top: 8 },
      });
      ed.onDidChangeModelContent(() => onChange && onChange(ed.getValue()));
      return {
        getValue: () => ed.getValue(),
        setValue: (v) => ed.setValue(v),
        setLanguage: (lang) => monaco.editor.setModelLanguage(ed.getModel(), lang),
        dispose: () => { ed.getModel() && ed.getModel().dispose(); ed.dispose(); },
        instance: ed,
        kind: "monaco",
      };
    } catch (e) {
      return fb;
    }
  }

  return { create };
})();
