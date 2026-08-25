from __future__ import annotations

from typing import Final

TUI_COLORS: Final[dict[str, str]] = {
    "canvas": "#101114",
    "surface": "#16181d",
    "surface-raised": "#1b1e25",
    "surface-hover": "#252934",
    "text": "#f1f3f7",
    "text-muted": "#a7adba",
    "text-quiet": "#737a89",
    "border": "#2a2e38",
    "border-strong": "#3b4150",
    "accent": "#7678f8",
    "accent-hover": "#9293ff",
    "accent-ink": "#17182d",
    "success": "#5ac998",
    "warning": "#e6ae5a",
    "danger": "#ec777f",
    "info": "#74afe9",
}

TUI_STATUS_LABELS: Final[dict[str, str]] = {
    "READY": "준비됨",
    "THINKING": "생성 중",
    "LOADING MODELS": "모델 불러오는 중",
    "SELECTING SESSION": "세션 선택 중",
    "WAITING FOR APPROVAL": "승인 필요",
    "AUTHENTICATING": "로그인 중",
    "COMPACTING CONTEXT": "대화 정리 중",
    "FLUSHING MEMORY": "메모리 저장 중",
    "RECOVERING AFTER OVERFLOW": "대화 복구 중",
    "ERROR": "오류",
}

TUI_CSS: Final[str] = (
    "Screen {\n"
    f"  background: {TUI_COLORS['canvas']};\n"
    f"  color: {TUI_COLORS['text']};\n"
    "}\n"
    "Footer {\n"
    f"  background: {TUI_COLORS['surface']};\n"
    f"  color: {TUI_COLORS['text-muted']};\n"
    "}\n"
    "#body { height: 1fr; min-height: 10; }\n"
    "#main-column { width: 1fr; height: 1fr; min-width: 36; padding: 1 2; }\n"
    "#conversation {\n"
    "  height: 1fr;\n"
    "  min-height: 2;\n"
    "  padding: 1;\n"
    f"  background: {TUI_COLORS['canvas']};\n"
    "  border: none;\n"
    "}\n"
    "#prompt {\n"
    "  height: 3;\n"
    "  margin-top: 0;\n"
    f"  border: round {TUI_COLORS['accent']};\n"
    f"  background: {TUI_COLORS['surface']};\n"
    f"  color: {TUI_COLORS['text']};\n"
    "}\n"
    "#command-preview {\n"
    "  display: none;\n"
    "  height: auto;\n"
    "  max-height: 6;\n"
    "  margin-top: 0;\n"
    "  padding: 0 1;\n"
    f"  border: round {TUI_COLORS['border-strong']};\n"
    f"  background: {TUI_COLORS['surface-raised']};\n"
    f"  color: {TUI_COLORS['text-muted']};\n"
    "}\n"
    "#compact-status {\n"
    "  display: block;\n"
    "  height: 1;\n"
    "  max-height: 1;\n"
    "  margin-top: 0;\n"
    "  padding: 0 1;\n"
    f"  color: {TUI_COLORS['text-muted']};\n"
    f"  background: {TUI_COLORS['surface']};\n"
    "  border-top: none;\n"
    "}\n"
    "#model-picker,\n"
    "#session-picker,\n"
    "#oauth-panel {\n"
    "  display: none;\n"
    "  height: auto;\n"
    "  max-height: 9;\n"
    "  margin-top: 0;\n"
    "  padding: 0 1;\n"
    f"  border: round {TUI_COLORS['border-strong']};\n"
    f"  background: {TUI_COLORS['surface-raised']};\n"
    "}\n"
    "#model-picker-title,\n"
    "#session-picker-title,\n"
    f"#oauth-title {{ color: {TUI_COLORS['info']}; text-style: bold; }}\n"
    "#model-options,\n"
    "#session-options { height: auto; min-height: 4; max-height: 7; }\n"
    "#settings-bar {\n"
    "  height: 1;\n"
    "  max-height: 1;\n"
    "  margin-top: 0;\n"
    "  padding: 0 1;\n"
    f"  color: {TUI_COLORS['text-quiet']};\n"
    f"  background: {TUI_COLORS['surface']};\n"
    "  border-top: none;\n"
    "}\n"
    ".section-label {\n"
    "  height: 1;\n"
    f"  color: {TUI_COLORS['text-muted']};\n"
    "  text-style: bold;\n"
    "}\n"
    "#oauth-panel { max-height: 4; margin-bottom: 1; }\n"
    f"#oauth-detail {{ color: {TUI_COLORS['text-muted']}; max-height: 1; overflow-y: auto; }}\n"
    "#approval-panel {\n"
    "  display: none;\n"
    "  height: auto;\n"
    "  margin-top: 0;\n"
    "  padding: 0 1;\n"
    "  max-height: 6;\n"
    f"  border: round {TUI_COLORS['warning']};\n"
    f"  background: {TUI_COLORS['surface-raised']};\n"
    "}\n"
    f"#approval-title {{ color: {TUI_COLORS['warning']}; text-style: bold; }}\n"
    "#approval-detail { margin: 0 0 1 0; max-height: 1; overflow-y: auto;\n"
    f"  color: {TUI_COLORS['text-muted']}; }}\n"
    "#approval-actions { height: 2; }\n"
    "#approve, #deny { height: 2; min-height: 2; padding: 0 1; border: none; }\n"
    f"#approve {{ width: 1fr; background: {TUI_COLORS['accent']};\n"
    f"  color: {TUI_COLORS['accent-ink']}; }}\n"
    f"#deny {{ width: 1fr; background: {TUI_COLORS['surface-hover']};\n"
    f"  color: {TUI_COLORS['danger']}; }}"
).strip()
