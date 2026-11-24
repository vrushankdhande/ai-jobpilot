import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppliedSwitch } from "./AppliedSwitch";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => { fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock); });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("AppliedSwitch", () => {
  it("can be switched off again: asks first, then undoes YOUR record", async () => {
    fetchMock.mockResolvedValue(json(200, { state: "MANUAL_HANDOFF_REQUIRED" }));
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const done = vi.fn();
    render(<AppliedSwitch jobId={"a".repeat(32)} title="ML Engineer" company="Acme" state="SUBMITTED" onRecorded={done} />);
    const sw = screen.getByRole("switch", { name: "Applied: ML Engineer at Acme" });
    expect(sw).toBeChecked();
    expect(sw).toBeEnabled();
    await userEvent.setup().click(sw);
    expect(confirm.mock.calls[0][0]).toMatch(/Mark "ML Engineer" at Acme as NOT applied\?/);
    await waitFor(() => expect(done).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/applications/undo-applied");
    expect(JSON.parse(init.body)).toEqual({ job_id: "a".repeat(32) });
  });

  it("does nothing when the question is cancelled, and shows the server's refusal", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const onError = vi.fn();
    const { rerender } = render(<AppliedSwitch jobId={"b".repeat(32)} title="X" state="SUBMITTED" onRecorded={() => {}} onError={onError} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("switch"));
    expect(fetchMock).not.toHaveBeenCalled();
    confirm.mockReturnValue(true);
    fetchMock.mockResolvedValue(json(409, { detail: "This application is verified (it has a confirmation reference or note), so it cannot be undone." }));
    rerender(<AppliedSwitch jobId={"b".repeat(32)} title="X" state="VERIFIED" onRecorded={() => {}} onError={onError} />);
    await user.click(screen.getByRole("switch"));
    await waitFor(() => expect(onError).toHaveBeenCalledWith(expect.stringMatching(/verified/)));
  });
});
