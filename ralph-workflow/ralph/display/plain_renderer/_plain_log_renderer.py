"""PlainLogRenderer - emit methods for plain log rendering."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.display.long_content_summary import build_ai_summary, build_headline_or_placeholder
from ralph.display.plain_renderer._activity_line_options import (
    ActivityLineOptions as _ActivityLineOptions,
)
from ralph.display.plain_renderer._constants import (
    _KIND_TO_LEVEL,
    _KIND_TO_TAG,
    _STREAMING_BLOCK_TAGS,
    _STREAMING_KINDS,
    LEVELS,
    TAG_CATEGORY,
    _sanitize,
)
from ralph.display.plain_renderer._phase_close_counters import _PhaseCloseCounters
from ralph.display.plain_renderer._phase_close_options import PhaseCloseOptions
from ralph.display.plain_renderer._phase_counters import PhaseCounters as _PhaseCounters
from ralph.display.plain_renderer._plain_log_renderer_base import _PlainLogRendererBase
from ralph.display.plain_renderer._streaming_ctx import _StreamingCtx

if TYPE_CHECKING:
    from ralph.display.phase_lifecycle import PhaseExitModel
    from ralph.display.plain_renderer._run_start_orientation import RunStartOrientation


class PlainLogRenderer(_PlainLogRendererBase):
    """Emit plain, ANSI-free structured log lines."""

    def emit_run_start(self, orientation: RunStartOrientation) -> None:
        """Emit a one-time MILESTONE orientation block at pipeline start."""
        timestamp = self._format_timestamp(self._clock())
        compact = self._ctx.mode == "compact"

        self._console.print(
            self._build_line(
                timestamp,
                "MILESTONE",
                "META",
                f"[run-start] {self._ctx.glyph_for('milestone')} Ralph Workflow run start",
            ),
            markup=False,
            highlight=False,
            no_wrap=True,
        )

        if orientation.legend_enabled and not compact:
            self._console.print(
                self._build_line(
                    timestamp,
                    "INFO",
                    "META",
                    "[run-start] legend: levels: INFO|SUCCESS|WARN|ERROR|MILESTONE"
                    "  cats: META|CONT  format: [tag][unit] message",
                ),
                markup=False,
                highlight=False,
                no_wrap=True,
            )

        if compact:
            self._emit_run_start_compact(timestamp, orientation)
        else:
            self._emit_run_start_wide(timestamp, orientation)

    def _emit_run_start_compact(self, timestamp: str, orientation: RunStartOrientation) -> None:
        """Compact layout: max 4 [run-start] lines (milestone + up to 3 content)."""
        prompt_ws_parts: list[str] = []
        if orientation.prompt_path is not None:
            prompt_ws_parts.append(f"prompt={_sanitize(orientation.prompt_path)}")
        if orientation.workspace_root is not None:
            prompt_ws_parts.append(f"workspace={_sanitize(orientation.workspace_root)}")
        if prompt_ws_parts:
            self._console.print(
                self._build_line(
                    timestamp, "INFO", "META", f"[run-start] {' '.join(prompt_ws_parts)}"
                ),
                markup=False,
                highlight=False,
                no_wrap=True,
            )

        agents_iters_parts: list[str] = []
        if orientation.developer_agent is not None:
            agents_iters_parts.append(f"developer={_sanitize(orientation.developer_agent)}")
        if orientation.developer_model is not None:
            agents_iters_parts.append(f"model={_sanitize(orientation.developer_model)}")
        iter_compact: list[str] = []
        if orientation.developer_iters is not None:
            iter_compact.append(f"dev:{orientation.developer_iters}")
        if iter_compact:
            agents_iters_parts.append(f"iterations={' '.join(iter_compact)}")
        if agents_iters_parts:
            self._console.print(
                self._build_line(
                    timestamp,
                    "INFO",
                    "META",
                    f"[run-start] {' '.join(agents_iters_parts)}",
                ),
                markup=False,
                highlight=False,
                no_wrap=True,
            )

        plan_val = "ready" if orientation.plan_present else "absent"
        misc_parts: list[str] = [f"plan={plan_val}"]
        if orientation.verbosity is not None:
            misc_parts.append(f"verbosity={orientation.verbosity}")
        if orientation.parallel_max_workers is not None:
            misc_parts.append(f"parallel=max_workers={orientation.parallel_max_workers}")
        self._console.print(
            self._build_line(timestamp, "INFO", "META", f"[run-start] {' '.join(misc_parts)}"),
            markup=False,
            highlight=False,
            no_wrap=True,
        )

    @staticmethod
    def _build_agents_parts(orientation: RunStartOrientation) -> list[str]:
        """Collect developer agent+model tokens for the wide run-start agents line."""
        parts: list[str] = []
        if orientation.developer_agent is not None:
            parts.append(f"developer={_sanitize(orientation.developer_agent)}")
        if orientation.developer_model is not None:
            parts.append(f"model={_sanitize(orientation.developer_model)}")
        return parts

    def _emit_run_start_wide(self, timestamp: str, orientation: RunStartOrientation) -> None:
        """Medium/wide layout: grouped fields on shared lines."""
        pw_parts: list[str] = []
        if orientation.prompt_path is not None:
            pw_parts.append(f"prompt={_sanitize(orientation.prompt_path)}")
        if orientation.workspace_root is not None:
            pw_parts.append(f"workspace={_sanitize(orientation.workspace_root)}")
        if pw_parts:
            self._console.print(
                self._build_line(timestamp, "INFO", "META", f"[run-start] {' '.join(pw_parts)}"),
                markup=False,
                highlight=False,
                no_wrap=True,
            )

        agents_parts = self._build_agents_parts(orientation)
        if agents_parts:
            self._console.print(
                self._build_line(
                    timestamp, "INFO", "META", f"[run-start] {' '.join(agents_parts)}"
                ),
                markup=False,
                highlight=False,
                no_wrap=True,
            )

        iter_parts: list[str] = []
        if orientation.developer_iters is not None:
            iter_parts.append(f"dev:{orientation.developer_iters}")
        if iter_parts:
            self._console.print(
                self._build_line(
                    timestamp,
                    "INFO",
                    "META",
                    f"[run-start] iterations={' '.join(iter_parts)}",
                ),
                markup=False,
                highlight=False,
                no_wrap=True,
            )

        if orientation.parallel_max_workers is not None:
            self._console.print(
                self._build_line(
                    timestamp,
                    "INFO",
                    "META",
                    f"[run-start] parallel=max_workers={orientation.parallel_max_workers}",
                ),
                markup=False,
                highlight=False,
                no_wrap=True,
            )

        plan_val = "ready" if orientation.plan_present else "absent"
        plan_parts: list[str] = [f"plan={plan_val}"]
        if orientation.verbosity is not None:
            plan_parts.append(f"verbosity={orientation.verbosity}")
        self._console.print(
            self._build_line(timestamp, "INFO", "META", f"[run-start] {' '.join(plan_parts)}"),
            markup=False,
            highlight=False,
            no_wrap=True,
        )

    def begin_phase(self, phase: str) -> None:
        """Start timing a new phase and reset its counters to zero."""
        self._phase_counters = _PhaseCounters(start_time=self._monotonic())
        self._last_phase_artifact_outcome = ""
        self._phase_close_emitted = False
        if self._run_start_time is None:
            self._run_start_time = self._monotonic()

    def emit_phase_close(
        self,
        phase: str,
        produced: str,
        *,
        options: PhaseCloseOptions | None = None,
    ) -> None:
        """Emit a single-line recap after a phase's artifact blocks are rendered."""
        opts = options or PhaseCloseOptions()
        self.flush_blocks()
        timestamp = self._format_timestamp(self._clock())
        clean_produced = _sanitize(produced).strip()
        counters = self._phase_counters
        if counters is not None:
            elapsed_s = round(max(0.0, self._monotonic() - counters.start_time), 1)
        else:
            elapsed_s = 0.0
            counters = _PhaseCounters()
        if opts.counter_overrides is not None:
            cb = (
                opts.counter_overrides.content_blocks
                if opts.counter_overrides.content_blocks
                else counters.content_blocks
            )
            tb = (
                opts.counter_overrides.thinking_blocks
                if opts.counter_overrides.thinking_blocks
                else counters.thinking_blocks
            )
            tc = (
                opts.counter_overrides.tool_calls
                if opts.counter_overrides.tool_calls
                else counters.tool_calls
            )
            err = (
                opts.counter_overrides.errors if opts.counter_overrides.errors else counters.errors
            )
        else:
            cb = counters.content_blocks
            tb = counters.thinking_blocks
            tc = counters.tool_calls
            err = counters.errors
        exit_part = f" exit={opts.exit_trigger}" if opts.exit_trigger is not None else ""
        suffix = (
            f"{exit_part} (elapsed={elapsed_s}s, content_blocks={cb},"
            f" thinking_blocks={tb}, tool_calls={tc},"
            f" errors={err})"
        )
        glyph_prefix = (
            f"{self._ctx.glyph_for('milestone')} "
            if opts.phase_role is not None and LEVELS.get(opts.phase_role) == "MILESTONE"
            else ""
        )
        iter_labels = ""
        if opts.iteration_context is not None and opts.iteration_context.has_context():
            iter_labels = " " + " ".join(
                f"[{label}]" for label, _ in opts.iteration_context.context_labels()
            )
        if clean_produced:
            line_suffix = (
                f"[phase-close] {glyph_prefix}phase={phase}{iter_labels} {clean_produced}{suffix}"
            )
        else:
            line_suffix = f"[phase-close] {glyph_prefix}phase={phase}{iter_labels}{suffix}"
        self._console.print(
            self._build_line(timestamp, "INFO", "META", line_suffix),
            markup=False,
            highlight=False,
            no_wrap=True,
        )
        self._last_phase_saved_counters = counters
        self._last_phase_elapsed_seconds = elapsed_s
        self._phase_counters = None

    @property
    def last_phase_elapsed_seconds(self) -> float:
        """Return elapsed time of the most recently closed phase in seconds."""
        return self._last_phase_elapsed_seconds

    @property
    def last_phase_counters(self) -> _PhaseCounters | None:
        """Return the counters from the most recently closed phase."""
        return self._last_phase_saved_counters

    @property
    def last_phase_artifact_outcome(self) -> str:
        """Return the artifact outcome from the most recently closed phase."""
        return self._last_phase_artifact_outcome

    @property
    def phase_close_emitted(self) -> bool:
        """Return True when emit_phase_close_from_exit has been called for the current phase."""
        return self._phase_close_emitted

    def record_artifact_outcome(self, outcome: str) -> None:
        """Record artifact outcome for retrieval at phase-close time without emitting a log line."""
        self._last_phase_artifact_outcome = outcome

    def emit_phase_close_from_exit(self, exit_model: PhaseExitModel) -> None:
        """Emit a phase-close recap from a PhaseExitModel."""
        self._last_phase_artifact_outcome = exit_model.artifact_outcome
        self._phase_close_emitted = True
        iter_ctx = exit_model.to_iteration_context()
        counter_overrides = None
        if (
            exit_model.content_blocks > 0
            or exit_model.thinking_blocks > 0
            or exit_model.tool_calls > 0
            or exit_model.errors > 0
        ):
            counter_overrides = _PhaseCloseCounters(
                content_blocks=exit_model.content_blocks,
                thinking_blocks=exit_model.thinking_blocks,
                tool_calls=exit_model.tool_calls,
                errors=exit_model.errors,
            )
        self.emit_phase_close(
            exit_model.phase_name,
            exit_model.artifact_outcome,
            options=PhaseCloseOptions(
                phase_role=exit_model.phase_role,
                iteration_context=iter_ctx if iter_ctx.has_context() else None,
                exit_trigger=exit_model.exit_trigger,
                counter_overrides=counter_overrides,
            ),
        )
        if exit_model.waiting_status_line or exit_model.last_failure_category:
            timestamp = self._format_timestamp(self._clock())
            debug_parts: list[str] = []
            if exit_model.waiting_status_line:
                debug_parts.append(f"waiting={_sanitize(exit_model.waiting_status_line)}")
            if exit_model.last_failure_category:
                debug_parts.append(
                    f"failure_category={_sanitize(exit_model.last_failure_category)}"
                )
            self._console.print(
                self._build_line(
                    timestamp,
                    "WARN",
                    "META",
                    f"[phase-close] debug phase={exit_model.phase_name} {' '.join(debug_parts)}",
                ),
                markup=False,
                highlight=False,
                no_wrap=True,
            )
        if exit_model.review_issues_found is not None:
            timestamp = self._format_timestamp(self._clock())
            if exit_model.review_issues_found:
                review_text = "[phase-close] review: issues found"
            else:
                review_text = "[phase-close] review: clean"
            self._console.print(
                self._build_line(timestamp, "INFO", "META", review_text),
                markup=False,
                highlight=False,
                no_wrap=True,
            )

    def _update_counters(self, kind: str, is_new_block: bool) -> None:
        """Increment activity counters for a new streaming block."""
        if kind == "text" and is_new_block:
            self._run_counters.content_blocks += 1
            if self._phase_counters is not None:
                self._phase_counters.content_blocks += 1
        elif kind == "thinking" and is_new_block:
            self._run_counters.thinking_blocks += 1
            if self._phase_counters is not None:
                self._phase_counters.thinking_blocks += 1
        elif kind == "tool_use":
            self._run_counters.tool_calls += 1
            if self._phase_counters is not None:
                self._phase_counters.tool_calls += 1
        elif kind == "error":
            self._run_counters.errors += 1
            if self._phase_counters is not None:
                self._phase_counters.errors += 1

    def emit_run_end(
        self,
        *,
        phase: str,
        total_agent_calls: int = 0,
        pr_url: str | None = None,
        exit_trigger: str | None = None,
        outer_dev_iteration: int | None = None,
    ) -> None:
        """Emit a one-time MILESTONE orientation block at pipeline stop."""
        self.flush_blocks()
        timestamp = self._format_timestamp(self._clock())
        total_elapsed_s = 0.0
        if self._run_start_time is not None:
            total_elapsed_s = round(max(0.0, self._monotonic() - self._run_start_time), 1)

        is_compact = self._ctx.mode == "compact"

        if is_compact:
            trigger_suffix = f" | {exit_trigger}" if exit_trigger is not None else ""
            self._console.print(
                self._build_line(
                    timestamp,
                    "MILESTONE",
                    "META",
                    f"[run-end] {phase} | {total_elapsed_s}s{trigger_suffix}",
                ),
                markup=False,
                highlight=False,
                no_wrap=True,
            )
            self._console.print(
                self._build_line(
                    timestamp,
                    "INFO",
                    "META",
                    f"[run-end] agent={total_agent_calls}"
                    f" content={self._run_counters.content_blocks}"
                    f" thinking={self._run_counters.thinking_blocks}"
                    f" tools={self._run_counters.tool_calls}"
                    f" errors={self._run_counters.errors}",
                ),
                markup=False,
                highlight=False,
                no_wrap=True,
            )
            if pr_url is not None:
                self._console.print(
                    self._build_line(
                        timestamp, "INFO", "META", f"[run-end] pr={_sanitize(pr_url)}"
                    ),
                    markup=False,
                    highlight=False,
                    no_wrap=True,
                )
        else:
            self._console.print(
                self._build_line(
                    timestamp,
                    "MILESTONE",
                    "META",
                    f"[run-end] {self._ctx.glyph_for('milestone')} Ralph Workflow run end",
                ),
                markup=False,
                highlight=False,
                no_wrap=True,
            )
            phase_elapsed = f"[run-end] phase={phase} elapsed={total_elapsed_s}s"
            if exit_trigger is not None:
                phase_elapsed += f" exit={exit_trigger}"
            if outer_dev_iteration is not None:
                phase_elapsed += f" dev_cycle={outer_dev_iteration}"
            self._console.print(
                self._build_line(timestamp, "INFO", "META", phase_elapsed),
                markup=False,
                highlight=False,
                no_wrap=True,
            )
            self._console.print(
                self._build_line(
                    timestamp,
                    "INFO",
                    "META",
                    f"[run-end] agent_calls={total_agent_calls}"
                    f" content_blocks={self._run_counters.content_blocks}"
                    f" thinking_blocks={self._run_counters.thinking_blocks}"
                    f" tool_calls={self._run_counters.tool_calls}"
                    f" errors={self._run_counters.errors}",
                ),
                markup=False,
                highlight=False,
                no_wrap=True,
            )
            if pr_url is not None:
                self._console.print(
                    self._build_line(
                        timestamp, "INFO", "META", f"[run-end] pr={_sanitize(pr_url)}"
                    ),
                    markup=False,
                    highlight=False,
                    no_wrap=True,
                )

    @property
    def content_blocks_count(self) -> int:
        return self._run_counters.content_blocks

    @property
    def thinking_blocks_count(self) -> int:
        return self._run_counters.thinking_blocks

    @property
    def tool_calls_count(self) -> int:
        return self._run_counters.tool_calls

    @property
    def errors_count(self) -> int:
        return self._run_counters.errors

    @property
    def run_elapsed_seconds(self) -> float | None:
        if self._run_start_time is None:
            return None
        return max(0.0, self._monotonic() - self._run_start_time)

    def _close_block(self, unit_id: str, timestamp: str) -> None:
        """Close an active streaming block, emitting the end-line and optional AI summary."""
        if unit_id not in self._active_block:
            return
        base_tag, accumulated = self._active_block.pop(unit_id)
        self._last_checkpoint_chars.pop(unit_id, None)
        block_tags = _STREAMING_BLOCK_TAGS.get(base_tag)
        if block_tags is None:
            return
        end_tag = block_tags[2]
        n = len(accumulated)
        chars = sum(len(x) for x in accumulated)
        joined = " ".join(accumulated)
        headline = build_headline_or_placeholder(joined, max_chars=self._ctx.headline_max_chars)
        self._console.print(
            self._build_line(
                timestamp,
                "INFO",
                "CONT",
                f"[{end_tag}][{unit_id}] ({n} fragments, {chars} chars) {headline}",
            ),
            markup=False,
            highlight=False,
            no_wrap=True,
        )

        if base_tag == "thinking":
            preview = build_headline_or_placeholder(joined, max_chars=self._ctx.headline_max_chars)
            preview_suffix = f"[{end_tag}][{unit_id}] ↳ preview: {_sanitize(preview)}"
            self._console.print(
                self._build_line(timestamp, "INFO", "CONT", preview_suffix),
                markup=False,
                highlight=False,
                no_wrap=True,
            )

        ai_summary = build_ai_summary(joined, self._ctx.env)
        if ai_summary:
            ai_text = _sanitize(ai_summary)
            self._console.print(
                self._build_line(
                    timestamp,
                    "INFO",
                    "CONT",
                    f"[{end_tag}][{unit_id}] ↳ ai-summary: {ai_text}",
                ),
                markup=False,
                highlight=False,
                no_wrap=True,
            )

    def flush_blocks(self) -> None:
        """Close all open streaming blocks and refresh display context."""
        self._ctx = self._ctx.refreshed()
        timestamp = self._format_timestamp(self._clock())
        unit_ids = list(self._active_block.keys())
        for unit_id in unit_ids:
            self._close_block(unit_id, timestamp)
        self._last_emitted_tool_signature.clear()

    def _handle_new_streaming_block(
        self,
        ctx: _StreamingCtx,
        start_tag: str,
    ) -> tuple[str, str | None]:
        """Open a new streaming block and return (tag, sanitized_override | None)."""
        self._active_block[ctx.unit_id] = (ctx.base_tag, [ctx.content])
        self._last_checkpoint_chars[ctx.unit_id] = 0
        self._update_counters(ctx.kind, is_new_block=True)
        if ctx.kind == "thinking":
            headline = build_headline_or_placeholder(
                ctx.content, max_chars=self._ctx.headline_max_chars
            )
            return start_tag, f"↳ preview: {_sanitize(headline)}"
        return start_tag, None

    def _continue_streaming_block(
        self,
        ctx: _StreamingCtx,
        accumulated: list[str],
        continue_tag: str,
    ) -> tuple[str, str | None] | None:
        """Continue an existing streaming block; returns (tag, override) or None for dedup."""
        if self._ctx.streaming_dedup_enabled and accumulated and accumulated[-1] == ctx.content:
            return None
        seq = len(accumulated) + 1
        accumulated.append(ctx.content)
        tag = f"{continue_tag}#{seq}"
        if self._ctx.streaming_checkpoints_enabled:
            total_chars = sum(len(x) for x in accumulated)
            last_cp = self._last_checkpoint_chars.get(ctx.unit_id, 0)
            emit_checkpoint = (
                seq % self._ctx.streaming_checkpoint_fragments == 0
                or total_chars - last_cp >= self._ctx.streaming_checkpoint_chars
            )
            if emit_checkpoint:
                self._last_checkpoint_chars[ctx.unit_id] = total_chars
                headline = build_headline_or_placeholder(
                    " ".join(accumulated), max_chars=self._ctx.headline_max_chars
                )
                cp_tag = f"{ctx.base_tag}-checkpoint#{seq}"
                cp_suffix = (
                    f"[{cp_tag}][{ctx.unit_id}] ({seq} fragments, {total_chars} chars) {headline}"
                )
                self._console.print(
                    self._build_line(ctx.timestamp, "INFO", "CONT", cp_suffix),
                    markup=False,
                    highlight=False,
                    no_wrap=True,
                )
                if ctx.kind == "thinking":
                    preview = build_headline_or_placeholder(
                        " ".join(accumulated), max_chars=self._ctx.headline_max_chars
                    )
                    preview_suffix = f"[{cp_tag}][{ctx.unit_id}] ↳ preview: {_sanitize(preview)}"
                    self._console.print(
                        self._build_line(ctx.timestamp, "INFO", "CONT", preview_suffix),
                        markup=False,
                        highlight=False,
                        no_wrap=True,
                    )
        thinking_min = self._ctx.thinking_preview_min_chars
        if ctx.kind == "thinking" and len(ctx.content) >= thinking_min:
            preview = build_headline_or_placeholder(
                ctx.content, max_chars=self._ctx.headline_max_chars
            )
            return tag, f"↳ preview: {_sanitize(preview)}"
        return tag, None

    def _process_streaming_block(
        self,
        ctx: _StreamingCtx,
        block_tags: tuple[str, str, str],
    ) -> tuple[str, str | None] | None:
        """Dispatch streaming block state; returns (tag, override) or None on dedup/early-return."""
        start_tag, continue_tag, _end_tag = block_tags
        for other_uid in [uid for uid in self._active_block if uid != ctx.unit_id]:
            self._close_block(other_uid, ctx.timestamp)
        if ctx.unit_id not in self._active_block:
            return self._handle_new_streaming_block(ctx, start_tag)
        existing_base_tag, accumulated = self._active_block[ctx.unit_id]
        if existing_base_tag != ctx.base_tag:
            self._close_block(ctx.unit_id, ctx.timestamp)
            return self._handle_new_streaming_block(ctx, start_tag)
        return self._continue_streaming_block(ctx, accumulated, continue_tag)

    def _emit_activity_supplements(
        self,
        unit_id: str,
        timestamp: str,
        tag: str,
        cat: str,
        opts: _ActivityLineOptions,
    ) -> None:
        """Emit optional summary and ai-summary lines before the main activity line."""
        if opts.summary_line is not None:
            if opts.summary_line:
                summary_text = _sanitize(opts.summary_line)
                self._console.print(
                    self._build_line(
                        timestamp, "INFO", cat, f"[{tag}][{unit_id}] ↳ summary: {summary_text}"
                    ),
                    markup=False,
                    highlight=False,
                    no_wrap=True,
                )
            elif opts.condensed_flag:
                self._console.print(
                    self._build_line(
                        timestamp,
                        "INFO",
                        cat,
                        f"[{tag}][{unit_id}] ↳ summary: (no headline available)",
                    ),
                    markup=False,
                    highlight=False,
                    no_wrap=True,
                )
        if opts.ai_summary_line:
            ai_text = _sanitize(opts.ai_summary_line)
            self._console.print(
                self._build_line(
                    timestamp, "INFO", cat, f"[{tag}][{unit_id}] ↳ ai-summary: {ai_text}"
                ),
                markup=False,
                highlight=False,
                no_wrap=True,
            )

    def emit_activity_line(
        self,
        unit_id: str,
        kind: str,
        content: str,
        *,
        options: _ActivityLineOptions | None = None,
        condensed_ref: str | None = None,
        condensed_flag: bool = False,
        summary_line: str | None = None,
        ai_summary_line: str | None = None,
        tool_signature: tuple[str, str] | None = None,
    ) -> None:
        """Emit a kind-tagged, level-badged content line."""
        if options is None:
            options = _ActivityLineOptions(
                condensed_ref=condensed_ref,
                condensed_flag=condensed_flag,
                summary_line=summary_line,
                ai_summary_line=ai_summary_line,
                tool_signature=tool_signature,
            )
        opts = options
        timestamp = self._format_timestamp(self._clock())
        base_tag = _KIND_TO_TAG.get(kind, "content")
        level = _KIND_TO_LEVEL.get(kind, "INFO")
        cat = TAG_CATEGORY.get(base_tag, "META")
        sanitized = _sanitize(content)
        if opts.condensed_ref is not None and opts.condensed_flag:
            sanitized = f"{sanitized} [see {opts.condensed_ref}]"

        if kind in _STREAMING_KINDS:
            if kind == "thinking" and not content.strip():
                return
            block_tags = _STREAMING_BLOCK_TAGS.get(base_tag)
            if block_tags is not None:
                ctx = _StreamingCtx(
                    unit_id=unit_id,
                    kind=kind,
                    content=content,
                    base_tag=base_tag,
                    timestamp=timestamp,
                )
                result = self._process_streaming_block(ctx, block_tags)
                if result is None:
                    return
                tag, sanitized_override = result
                if sanitized_override is not None:
                    sanitized = sanitized_override
            else:
                tag = base_tag
                self._update_counters(kind, is_new_block=False)
        else:
            for uid in list(self._active_block.keys()):
                self._close_block(uid, timestamp)
            tag = base_tag
            self._update_counters(kind, is_new_block=False)

        if kind == "tool_use" and opts.tool_signature is not None:
            tool_name, tool_path = opts.tool_signature
            self._last_emitted_tool_signature[unit_id] = (tool_name, tool_path)

        self._emit_activity_supplements(unit_id, timestamp, tag, cat, opts)

        self._console.print(
            self._build_line(timestamp, level, cat, f"[{tag}][{unit_id}] {sanitized}"),
            markup=False,
            highlight=False,
            no_wrap=True,
        )

    def emit_log_line(self, unit_id: str, line: str) -> None:
        self.emit_activity_line(unit_id, "raw", line)

    def emit_status_line(self, unit_id: str, status: str) -> None:
        sanitized = _sanitize(status)
        self._console.out(f"[{unit_id}] status={sanitized}")

    def emit_artifact(self, kind: str, summary: str) -> None:
        """Emit an artifact summary line for copy-paste-safe transcripts."""
        timestamp = self._format_timestamp(self._clock())
        sanitized_summary = _sanitize(summary)
        self._console.print(
            self._build_line(
                timestamp,
                "INFO",
                "META",
                f"[artifact] kind={kind} summary={sanitized_summary}",
            ),
            markup=False,
            highlight=False,
            no_wrap=True,
        )

    def emit_warn_line(self, unit_id: str, tag: str, message: str) -> None:
        """Emit a WARN META line for a specific tag."""
        timestamp = self._format_timestamp(self._clock())
        cat = TAG_CATEGORY.get(tag, "META")
        self._console.print(
            self._build_line(timestamp, "WARN", cat, f"[{tag}][{unit_id}] {message}"),
            markup=False,
            highlight=False,
            no_wrap=True,
        )
