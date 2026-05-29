"""
Scope resolution for OSWorld actions.

Maps action arguments and context to resource scopes for transaction isolation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Set


class ScopeLevel(Enum):
    """Granularity levels for UI scopes."""

    SYSTEM = "system"  # OS-level (clipboard, system settings)
    APPLICATION = "application"  # Per-app state
    DOCUMENT = "document"  # Per-document/tab within app
    ELEMENT = "element"  # UI element level
    NONE = "none"  # No scope (read-only)


@dataclass
class UIScope:
    """Structured scope for UI state."""

    level: ScopeLevel
    app_name: Optional[str] = None
    document_id: Optional[str] = None
    element_id: Optional[str] = None

    def to_resource_id(self) -> str:
        """Convert to Atomix resource ID string."""
        parts = ["osworld", self.level.value]
        if self.app_name:
            parts.append(self.app_name)
        if self.document_id:
            parts.append(self.document_id)
        if self.element_id:
            parts.append(self.element_id)
        return ":".join(parts)


class ScopeResolver:
    """Resolves action arguments to resource scopes."""

    # Application detection patterns (from window titles)
    APP_PATTERNS: Dict[str, List[str]] = {
        "libreoffice": ["LibreOffice", "Writer", "Calc", "Impress", "Draw"],
        "chrome": ["Chrome", "Chromium", "Google Chrome"],
        "firefox": ["Firefox", "Mozilla Firefox"],
        "vscode": ["Visual Studio Code", "VSCode", "Code -"],
        "gimp": ["GIMP", "GNU Image"],
        "vlc": ["VLC media player", "VLC"],
        "nautilus": ["Files", "Nautilus", "File Manager"],
        "terminal": ["Terminal", "gnome-terminal", "konsole", "xterm"],
        "thunderbird": ["Thunderbird", "Mozilla Thunderbird"],
        "gedit": ["gedit", "Text Editor"],
    }

    def resolve(
        self,
        action_type: str,
        args: Dict,
        context: Dict,
    ) -> Set[str]:
        """Resolve scopes based on action and context."""
        scopes = set()

        # Get active application from context
        active_window = context.get("active_window_title", "")
        app_name = self._detect_app(active_window)

        # Get document context if available
        document_id = context.get("document_path") or context.get("active_tab")

        # Build appropriate scope based on action type
        if action_type in ["click", "double_click", "right_click"]:
            scope = UIScope(
                level=ScopeLevel.APPLICATION,
                app_name=app_name,
            )
            scopes.add(scope.to_resource_id())

        elif action_type in ["typing", "press"]:
            scope = UIScope(
                level=ScopeLevel.DOCUMENT,
                app_name=app_name,
                document_id=document_id,
            )
            scopes.add(scope.to_resource_id())

        elif action_type == "hotkey":
            keys = [k.lower() for k in args.get("keys", [])]

            # Clipboard operations have system-level scope
            if "ctrl" in keys and ("c" in keys or "v" in keys or "x" in keys):
                scopes.add(UIScope(level=ScopeLevel.SYSTEM, app_name="clipboard").to_resource_id())

            # Document-modifying hotkeys
            if "ctrl" in keys and ("s" in keys or "z" in keys or "y" in keys):
                scopes.add(
                    UIScope(
                        level=ScopeLevel.DOCUMENT,
                        app_name=app_name,
                        document_id=document_id,
                    ).to_resource_id()
                )

            # If no specific scope, use application level
            if not scopes:
                scopes.add(UIScope(level=ScopeLevel.APPLICATION, app_name=app_name).to_resource_id())

        elif action_type == "scroll":
            scope = UIScope(
                level=ScopeLevel.APPLICATION,
                app_name=app_name,
                element_id="viewport",
            )
            scopes.add(scope.to_resource_id())

        elif action_type in ["drag_to"]:
            # Drag affects application state
            scope = UIScope(
                level=ScopeLevel.APPLICATION,
                app_name=app_name,
            )
            scopes.add(scope.to_resource_id())

        elif action_type in ["move_to", "wait", "done", "fail"]:
            # No side effects
            pass

        return scopes

    def _detect_app(self, window_title: str) -> str:
        """Detect application from window title."""
        if not window_title:
            return "unknown"

        window_lower = window_title.lower()
        for app, patterns in self.APP_PATTERNS.items():
            if any(p.lower() in window_lower for p in patterns):
                return app

        return "unknown"

    def get_app_category(self, app_name: str) -> str:
        """Categorize app for scope grouping."""
        file_centric = {"libreoffice", "vscode", "gedit", "nautilus"}
        browser = {"chrome", "firefox"}
        media = {"vlc", "gimp"}
        communication = {"thunderbird"}
        system = {"terminal", "nautilus"}

        if app_name in file_centric:
            return "file_centric"
        elif app_name in browser:
            return "browser"
        elif app_name in media:
            return "media"
        elif app_name in communication:
            return "communication"
        elif app_name in system:
            return "system"
        else:
            return "other"


# Global resolver instance
scope_resolver = ScopeResolver()
