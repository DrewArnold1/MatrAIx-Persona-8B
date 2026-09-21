import { useState } from "react";

import { useI18n } from "@/i18n/I18nProvider";
import { createAdhocSurveyQuestion } from "@/lib/api";
import { ApiError } from "@/lib/api";
import type { AdhocSurveyQuestionResponse } from "@/lib/types";
import { FOCUS_RING, Sym } from "../cockpitShared";

/** Blank option rows are dropped on submit; this is just the starting shape. */
const INITIAL_OPTIONS = ["", ""];
const MAX_OPTIONS = 12;

export interface AskQuestionFormProps {
  /** Called with the materialized task once the backend has written it. */
  onCreated: (result: AdhocSurveyQuestionResponse) => void;
  onCancel: () => void;
  disabled?: boolean;
}

/**
 * Ask one question of a persona cohort without authoring a task folder.
 *
 * Posts to `/api/survey-eval/adhoc-questions`, which writes a real survey task.
 * The caller refreshes the task list and selects the returned task path; from
 * there the ordinary sampling and launch path takes over.
 */
export function AskQuestionForm({ onCreated, onCancel, disabled }: AskQuestionFormProps) {
  const { t } = useI18n();
  const [question, setQuestion] = useState("");
  const [options, setOptions] = useState<string[]>(INITIAL_OPTIONS);
  const [freeText, setFreeText] = useState(false);
  const [contextNote, setContextNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const filledOptions = options.map((value) => value.trim()).filter(Boolean);
  const distinctOptions = new Set(filledOptions);
  const duplicateOptions = distinctOptions.size !== filledOptions.length;
  const canSubmit =
    !submitting &&
    !disabled &&
    question.trim().length > 0 &&
    (freeText || (filledOptions.length >= 2 && !duplicateOptions));

  const setOptionAt = (index: number, value: string) => {
    setOptions((current) => current.map((item, i) => (i === index ? value : item)));
  };

  const removeOptionAt = (index: number) => {
    setOptions((current) =>
      current.length <= 2 ? current.map((item, i) => (i === index ? "" : item)) : current.filter((_, i) => i !== index),
    );
  };

  const submit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const result = await createAdhocSurveyQuestion({
        question: question.trim(),
        // null, not [], is what asks the backend for a free-text question.
        options: freeText ? null : filledOptions,
        contextNote: contextNote.trim(),
      });
      onCreated(result);
    } catch (exc) {
      setError(
        exc instanceof ApiError ? exc.message : t("askQuestion.error.generic"),
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <label className="flex flex-col gap-1.5">
        <span className="hud text-[11px] uppercase tracking-wide text-text-dim">
          {t("askQuestion.question.label")}
        </span>
        <textarea
          value={question}
          disabled={disabled || submitting}
          onChange={(event) => setQuestion(event.target.value)}
          rows={3}
          placeholder={t("askQuestion.question.placeholder")}
          className="glass-tile w-full rounded-lg px-2.5 py-2 text-[14px] text-text-main placeholder:text-text-dim"
        />
      </label>

      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between gap-2">
          <span className="hud text-[11px] uppercase tracking-wide text-text-dim">
            {t("askQuestion.options.label")}
          </span>
          <button
            type="button"
            disabled={disabled || submitting}
            onClick={() => setFreeText((value) => !value)}
            className={`rounded-md border border-outline px-2 py-1 text-[11px] text-text-variant transition hover:border-primary hover:text-text-main ${FOCUS_RING}`}
          >
            {freeText ? t("askQuestion.options.useChoices") : t("askQuestion.options.useFreeText")}
          </button>
        </div>

        {freeText ? (
          // Say what free text costs here, not after the run: a free-text
          // question aggregates as themes, never as percentages.
          <p className="rounded-md border border-outline/60 bg-surface-low px-2.5 py-2 text-[12px] leading-snug text-text-variant">
            {t("askQuestion.options.freeTextHint")}
          </p>
        ) : (
          <>
            {options.map((option, index) => (
              <div key={index} className="flex items-center gap-1.5">
                <input
                  type="text"
                  value={option}
                  disabled={disabled || submitting}
                  onChange={(event) => setOptionAt(index, event.target.value)}
                  placeholder={t("askQuestion.options.placeholder", { index: index + 1 })}
                  className="glass-tile h-9 min-w-0 flex-1 rounded-lg px-2.5 text-[14px] text-text-main placeholder:text-text-dim"
                />
                <button
                  type="button"
                  disabled={disabled || submitting}
                  onClick={() => removeOptionAt(index)}
                  aria-label={t("askQuestion.options.remove", { index: index + 1 })}
                  className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-outline text-text-variant transition hover:border-primary hover:text-text-main ${FOCUS_RING}`}
                >
                  <Sym name="close" size={16} />
                </button>
              </div>
            ))}
            {options.length < MAX_OPTIONS && (
              <button
                type="button"
                disabled={disabled || submitting}
                onClick={() => setOptions((current) => [...current, ""])}
                className={`flex items-center gap-1.5 self-start rounded-md border border-outline px-2 py-1 text-[12px] text-text-variant transition hover:border-primary hover:text-text-main ${FOCUS_RING}`}
              >
                <Sym name="add" size={16} />
                {t("askQuestion.options.add")}
              </button>
            )}
            {duplicateOptions && (
              <p className="text-[12px] text-warn">{t("askQuestion.error.duplicateOptions")}</p>
            )}
          </>
        )}
      </div>

      <label className="flex flex-col gap-1.5">
        <span className="hud text-[11px] uppercase tracking-wide text-text-dim">
          {t("askQuestion.context.label")}
        </span>
        <textarea
          value={contextNote}
          disabled={disabled || submitting}
          onChange={(event) => setContextNote(event.target.value)}
          rows={2}
          placeholder={t("askQuestion.context.placeholder")}
          className="glass-tile w-full rounded-lg px-2.5 py-2 text-[14px] text-text-main placeholder:text-text-dim"
        />
      </label>

      <p className="rounded-md border border-outline/60 bg-surface-low px-2.5 py-2 text-[12px] leading-snug text-text-variant">
        {t("askQuestion.caveat")}
      </p>

      {error && (
        <p role="alert" className="text-[12px] text-danger">
          {error}
        </p>
      )}

      <div className="flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          disabled={submitting}
          className={`rounded-md border border-outline px-3 py-1.5 text-[13px] text-text-variant transition hover:border-primary hover:text-text-main ${FOCUS_RING}`}
        >
          {t("askQuestion.cancel")}
        </button>
        <button
          type="button"
          onClick={submit}
          disabled={!canSubmit}
          className={`rounded-md border border-primary bg-primary/15 px-3 py-1.5 text-[13px] font-semibold text-text-main transition hover:bg-primary/25 disabled:cursor-not-allowed disabled:opacity-50 ${FOCUS_RING}`}
        >
          {submitting ? t("askQuestion.submitting") : t("askQuestion.submit")}
        </button>
      </div>
    </div>
  );
}
