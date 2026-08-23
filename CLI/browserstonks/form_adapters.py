"""Form adapters: plug-n-play site integrations.

Each adapter takes the DOM state sent by an integration and produces a
normalized form description:

    {
      "site": "google_forms",
      "url": ...,
      "fields": [
        {"id": "...", "label": "Your name", "type": "text",
         "options": [...], "required": bool, "selector": "..."},
      ]
    }

New sites = new class + entry in BUILTIN_ADAPTERS, or a drop-in file in
<root>/plugins/ registering browserstonks_form_adapters.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse


class BaseAdapter:
    name = "base"
    """Return a normalized description, or None if this adapter doesn't match."""
    def matches(self, dom: dict[str, Any]) -> bool:
        raise NotImplementedError

    def describe(self, dom: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class GenericHTMLForms(BaseAdapter):
    """Fallback: plain HTML <form>/<input>/<select>/<textarea> snapshots."""
    name = "generic"

    def matches(self, dom: dict[str, Any]) -> bool:
        return bool(dom.get("forms"))

    def describe(self, dom: dict[str, Any]) -> dict[str, Any]:
        fields = []
        for form in dom.get("forms", []):
            for f in form.get("fields", []):
                fields.append({
                    "id": f.get("id") or f.get("name") or f.get("selector"),
                    "label": f.get("label", ""),
                    "type": f.get("type", "text"),
                    "options": f.get("options", []),
                    "required": bool(f.get("required")),
                    "selector": f.get("selector"),
                    "value": f.get("value"),  # pre-filled text; planner skips these
                    "form": form.get("id") or form.get("index"),
                })
        return {"site": self.name, "url": dom.get("url", ""), "fields": fields}


class GoogleFormsAdapter(BaseAdapter):
    name = "google_forms"

    def matches(self, dom: dict[str, Any]) -> bool:
        host = urlparse(dom.get("url", "")).netloc
        return host.endswith("docs.google.com")

    def describe(self, dom: dict[str, Any]) -> dict[str, Any]:
        fields = []
        for i, f in enumerate(dom.get("forms", [])):
            label = f.get("label", "")
            ftype = self._normalize_type(f.get("role", ""), label)
            fields.append({
                "id": f.get("id") or f"q{i}",
                "label": label,
                "type": ftype,
                "options": f.get("options", []),
                "required": "必須" in label or "*" in label or f.get("required"),
                "selector": f.get("selector"),
                "value": f.get("value"),
                "form": "google-form",
            })
        return {"site": self.name, "url": dom.get("url", ""), "fields": fields}

    @staticmethod
    def _normalize_type(role: str, label: str) -> str:
        r = role.lower()
        if "radio" in r:
            return "radio"
        if "checkbox" in r:
            return "checkbox"
        if "select" in r or "dropdown" in r:
            return "select"
        if "date" in r:
            return "date"
        label_l = label.lower()
        if "email" in label_l:
            return "email"
        if "phone" in label_l or "mobile" in label_l:
            return "tel"
        if "number" in label_l:
            return "number"
        if "long" in label_l or "paragraph" in label_l:
            return "textarea"
        return "text"


class TypeformAdapter(BaseAdapter):
    name = "typeform"

    def matches(self, dom: dict[str, Any]) -> bool:
        host = urlparse(dom.get("url", "")).netloc
        return "typeform" in host or dom.get("site") == "typeform"

    def describe(self, dom: dict[str, Any]) -> dict[str, Any]:
        fields = []
        for i, f in enumerate(dom.get("forms", [])):
            fields.append({
                "id": f.get("id") or f"tf{i}",
                "label": f.get("label", ""),
                "type": f.get("type", "text"),
                "options": f.get("options", []),
                "required": bool(f.get("required")),
                "selector": f.get("selector"),
                "value": f.get("value"),
                "form": "typeform",
            })
        return {"site": self.name, "url": dom.get("url", ""), "fields": fields}


BUILTIN_ADAPTERS: dict[str, type[BaseAdapter]] = {
    "generic": GenericHTMLForms,
    "google_forms": GoogleFormsAdapter,
    "typeform": TypeformAdapter,
}


def detect_site(dom: dict[str, Any]) -> str:
    """Match URL / markers against known form-builder sites."""
    url = dom.get("url", "")
    host = urlparse(url).netloc
    if host.endswith("docs.google.com"):
        return "google_forms"
    if "typeform" in host:
        return "typeform"
    if dom.get("site"):
        return str(dom["site"])
    return "generic"


def extract_site_adapter(dom: dict[str, Any]) -> BaseAdapter:
    return BUILTIN_ADAPTERS[detect_site(dom)]()


# Convenience re-export used by tests
GoogleFormsAdapter._re = re  # keep import used under all linters
