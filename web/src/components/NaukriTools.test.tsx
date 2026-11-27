import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NaukriSearch } from "./NaukriTools";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => { fetchMock = vi.fn().mockResolvedValue(json(201, { action: "insert" })); vi.stubGlobal("fetch", fetchMock); });
afterEach(() => vi.unstubAllGlobals());


describe("NaukriSearch", () => {
  it("has no usable link until keywords are entered", () => {
    render(<NaukriSearch />);
    expect(screen.queryByRole("link", { name: /Open Naukri search/ })).toBeNull();
    expect(screen.getByRole("button", { name: /Open Naukri search/ })).toBeDisabled();
  });

  it("builds a Naukri link that opens in a new tab without leaking the opener, and requests nothing", async () => {
    render(<NaukriSearch />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Naukri keywords"), "AI engineer");
    await user.type(screen.getByLabelText("Naukri location"), "Pune");
    await user.type(screen.getByLabelText("Naukri experience (years)"), "3");
    await user.selectOptions(screen.getByLabelText("Naukri posted within"), "week");
    const a = screen.getByRole("link", { name: /Open Naukri search/ });
    const u = new URL(a.getAttribute("href")!);
    expect(u.origin + u.pathname).toBe("https://www.naukri.com/ai-engineer-jobs-in-pune");
    expect(u.searchParams.get("k")).toBe("AI engineer");
    expect(u.searchParams.get("l")).toBe("Pune");
    expect(u.searchParams.get("experience")).toBe("3");
    expect(u.searchParams.get("jobAge")).toBe("7");
    expect(a).toHaveAttribute("target", "_blank");
    expect(a.getAttribute("rel")).toContain("noopener");
    expect(a.getAttribute("rel")).toContain("noreferrer");
    expect(fetchMock).not.toHaveBeenCalled();                                        // this app never contacts Naukri (or anything) for a search link
  });
});

