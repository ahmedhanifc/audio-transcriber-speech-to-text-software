"""
general_llm_app/llm.py — A dead-simple LLM wrapper for notebook use.

Usage (from a notebook):
    from general_llm_app.llm import LLM

    llm = LLM()

    # Plain text call
    answer = llm.call("What is the capital of France?")

    # With a prompt template file
    answer = llm.call("tasks/hult_form/prompt.md", variables={"KPI_ID": "1"})

    # Structured output (JSON schema)
    data = llm.call("tasks/hult_form/prompt.md",
                     variables={"KPI_ID": "1"},
                     schema_path="tasks/hult_form/schema.json")

    # Batch over rows
    results = llm.batch("tasks/hult_form/prompt.md",
                        rows=df.to_dict("records"),
                        variables_fn=lambda row: {"KPI_ID": row["KPI ID"]},
                        schema_path="tasks/hult_form/schema.json")
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Optional

from openai import OpenAI


# ── Where this file lives (used to resolve relative paths) ────
_APP_DIR = Path(__file__).resolve().parent


class LLM:
    """OpenAI wrapper. One class, two methods: call() and batch()."""

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        system_prompt: str = "",
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt
        self.client = OpenAI()  # reads OPENAI_API_KEY from env

    # ── Main method ───────────────────────────────────────────

    def call(
        self,
        prompt: str,
        *,
        variables: Optional[dict[str, str]] = None,
        schema_path: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> str | dict:
        """
        Call the LLM with a prompt string or a path to a .md template.

        Parameters
        ----------
        prompt : str
            Either a raw prompt string, OR a path to a .md file (relative
            to general_llm_app/ or absolute).
        variables : dict, optional
            Key-value pairs to fill {{placeholders}} in the template.
        schema_path : str, optional
            Path to a .json schema file for structured output.
        system_prompt : str, optional
            Override the default system prompt for this call.

        Returns
        -------
        str if no schema, dict if schema is provided.
        """
        # Load prompt from file if it looks like a path
        prompt_text = self._resolve_prompt(prompt, variables)

        # Load schema if provided
        schema = self._load_json(schema_path) if schema_path else None

        # Build messages
        sys = system_prompt if system_prompt is not None else self.system_prompt
        messages = []
        if sys:
            messages.append({"role": "system", "content": sys})
        messages.append({"role": "user", "content": prompt_text})

        # Build params
        params: dict[str, Any] = {
            "model": self.model,
            "temperature": self.temperature,
        }
        if self.max_tokens:
            params["max_tokens"] = self.max_tokens

        if schema:
            params["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_output",
                    "strict": True,
                    "schema": schema,
                },
            }

        # Call the API
        response = self.client.chat.completions.create(
            messages=messages,
            **params,
        )

        content = response.choices[0].message.content

        if schema:
            return json.loads(content)
        return content

    # ── Batch method ──────────────────────────────────────────

    def batch(
        self,
        prompt: str,
        rows: list[dict],
        *,
        variables_fn: Optional[Callable[[dict], dict[str, str]]] = None,
        schema_path: Optional[str] = None,
        system_prompt: Optional[str] = None,
        delay: float = 0.5,
    ) -> list[dict | str]:
        """
        Call the LLM once per row, filling the template each time.

        Parameters
        ----------
        prompt : str
            Path to a .md template file, or a raw template string with
            {{placeholders}}.
        rows : list[dict]
            List of dicts (e.g. from df.to_dict("records")).
        variables_fn : callable(row) -> dict, optional
            Maps each row to template {{placeholder}} values.
            If None, uses the row keys directly.
        schema_path : str, optional
            Path to a JSON schema file for structured output.
        system_prompt : str, optional
            Override the default system prompt.
        delay : float
            Seconds to wait between API calls (rate limiting).

        Returns
        -------
        list of str or dict (one per row).
        """
        results = []
        total = len(rows)

        for i, row in enumerate(rows, 1):
            variables = variables_fn(row) if variables_fn else row
            label = variables.get("name", variables.get("id", f"row {i}"))
            print(f"  [{i}/{total}] {label}")

            result = self.call(
                prompt,
                variables=variables,
                schema_path=schema_path,
                system_prompt=system_prompt,
            )
            results.append(result)

            if i < total:
                time.sleep(delay)

        print(f"\n✅ Done — {len(results)} results.")
        return results

    # ── Internals ─────────────────────────────────────────────

    def _resolve_prompt(
        self, prompt: str, variables: Optional[dict[str, str]]
    ) -> str:
        """If prompt looks like a file path, load it. Then fill placeholders."""
        text = prompt

        # Check if it's a file path
        if prompt.endswith(".md") or prompt.endswith(".txt"):
            path = Path(prompt)
            if not path.is_absolute():
                path = _APP_DIR / path
            text = path.read_text(encoding="utf-8")

        # Fill {{placeholders}}
        if variables:
            for key, value in variables.items():
                text = text.replace("{{" + key + "}}", str(value))

        return text

    @staticmethod
    def _load_json(path: str) -> dict:
        """Load a JSON file, resolving relative paths from the app dir."""
        p = Path(path)
        if not p.is_absolute():
            p = _APP_DIR / p
        return json.loads(p.read_text(encoding="utf-8"))
