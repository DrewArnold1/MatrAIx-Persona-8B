// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { I18nProvider } from "@/i18n/I18nProvider";
import { AskQuestionForm } from "./AskQuestionForm";

const createAdhocSurveyQuestion = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, createAdhocSurveyQuestion };
});

afterEach(() => {
  cleanup();
  createAdhocSurveyQuestion.mockReset();
});

function renderForm(props: Partial<Parameters<typeof AskQuestionForm>[0]> = {}) {
  const onCreated = props.onCreated ?? vi.fn();
  const onCancel = props.onCancel ?? vi.fn();
  render(
    <I18nProvider>
      <AskQuestionForm onCreated={onCreated} onCancel={onCancel} {...props} />
    </I18nProvider>,
  );
  return { onCreated, onCancel };
}

function submitButton(): HTMLButtonElement {
  return screen.getByRole("button", { name: "Create question" }) as HTMLButtonElement;
}

function typeQuestion(text: string) {
  fireEvent.change(screen.getByPlaceholderText(/Is the country on the right track/), {
    target: { value: text },
  });
}

describe("AskQuestionForm", () => {
  it("cannot submit an empty question", () => {
    renderForm();
    expect((submitButton() as HTMLButtonElement).disabled).toBe(true);
  });

  it("cannot submit a choice question with fewer than two options", () => {
    renderForm();
    typeQuestion("Right track?");
    fireEvent.change(screen.getByPlaceholderText("Option 1"), {
      target: { value: "Yes" },
    });
    expect((submitButton() as HTMLButtonElement).disabled).toBe(true);
  });

  it("blocks duplicate options and says why", () => {
    renderForm();
    typeQuestion("Right track?");
    fireEvent.change(screen.getByPlaceholderText("Option 1"), { target: { value: "Yes" } });
    fireEvent.change(screen.getByPlaceholderText("Option 2"), { target: { value: "Yes" } });
    expect(screen.getByText("Options must be distinct.")).toBeTruthy();
    expect((submitButton() as HTMLButtonElement).disabled).toBe(true);
  });

  it("submits a choice question with its options", async () => {
    createAdhocSurveyQuestion.mockResolvedValue({
      taskPath: "application/tasks/survey_adhoc-abc",
      questionnaireId: "adhoc_abc_v1",
      folderName: "survey_adhoc-abc",
      questionnaire: {},
      reused: false,
      caveat: "",
    });
    const { onCreated } = renderForm();
    typeQuestion("Right track?");
    fireEvent.change(screen.getByPlaceholderText("Option 1"), { target: { value: "Yes" } });
    fireEvent.change(screen.getByPlaceholderText("Option 2"), { target: { value: "No" } });
    fireEvent.click(submitButton());

    await waitFor(() => expect(createAdhocSurveyQuestion).toHaveBeenCalledTimes(1));
    expect(createAdhocSurveyQuestion).toHaveBeenCalledWith({
      question: "Right track?",
      options: ["Yes", "No"],
      contextNote: "",
    });
    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
  });

  it("sends null options for a free-text question", async () => {
    // null, not [], is what asks the backend for a free-text question; an
    // empty array would be read as a choice question with no options.
    createAdhocSurveyQuestion.mockResolvedValue({
      taskPath: "application/tasks/survey_adhoc-def",
      questionnaireId: "adhoc_def_v1",
      folderName: "survey_adhoc-def",
      questionnaire: {},
      reused: false,
      caveat: "",
    });
    renderForm();
    typeQuestion("What worries you most?");
    fireEvent.click(screen.getByRole("button", { name: "Use free text" }));
    fireEvent.click(submitButton());

    await waitFor(() => expect(createAdhocSurveyQuestion).toHaveBeenCalledTimes(1));
    expect(createAdhocSurveyQuestion.mock.calls[0][0].options).toBeNull();
  });

  it("warns that free text is summarized rather than counted", () => {
    renderForm();
    fireEvent.click(screen.getByRole("button", { name: "Use free text" }));
    expect(screen.getByText(/summarized as themes, not counted as percentages/)).toBeTruthy();
  });

  it("surfaces a backend rejection instead of failing silently", async () => {
    createAdhocSurveyQuestion.mockRejectedValue(new Error("boom"));
    const { onCreated } = renderForm();
    typeQuestion("Right track?");
    fireEvent.change(screen.getByPlaceholderText("Option 1"), { target: { value: "Yes" } });
    fireEvent.change(screen.getByPlaceholderText("Option 2"), { target: { value: "No" } });
    fireEvent.click(submitButton());

    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(onCreated).not.toHaveBeenCalled();
  });

  it("shows the standing caveat before the question is even sent", () => {
    renderForm();
    expect(screen.getByText(/simulated distribution, not a survey estimate/)).toBeTruthy();
  });
});
