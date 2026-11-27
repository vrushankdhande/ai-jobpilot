import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, vi } from "vitest";
import { AuthGate, useAuth } from "./auth";

const calls: string[] = [];
function mock(status: any, login: { status: number; body: any } = { status: 200, body: { user: { user_id: "u", name: "Asha", role: "user" } } }) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    calls.push(`${init?.method ?? "GET"} ${url.pathname} ${init?.body ?? ""}`);
    const [code, body] = url.pathname === "/api/auth/status" ? [200, status] : url.pathname === "/api/auth/login" || url.pathname === "/api/auth/setup" ? [login.status, login.body] : [200, {}];
    return new Response(JSON.stringify(body), { status: code, headers: { "content-type": "application/json" } });
  }));
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); calls.length = 0; });

function Who() { const { user } = useAuth(); return <p>App for {user?.name ?? "nobody"}</p>; }

describe("AuthGate", () => {
  it("asks to sign in and then shows the app as that person, forgetting the previous person's saved values", async () => {
    mock({ auth_enabled: true, setup_needed: false, user: null });
    sessionStorage.setItem("discover-form", "previous person's search");
    render(<AuthGate><Who /></AuthGate>);
    await userEvent.type(await screen.findByLabelText("E-mail"), "asha@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "secret-pass");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("App for Asha")).toBeInTheDocument();
    expect(calls.some((c) => c.startsWith("POST /api/auth/login") && c.includes("asha@example.com"))).toBe(true);
    expect(sessionStorage.getItem("discover-form")).toBeNull();
  });

  it("shows the error and keeps the e-mail when the password is wrong", async () => {
    mock({ auth_enabled: true, setup_needed: false, user: null }, { status: 401, body: { detail: "Wrong e-mail or password." } });
    render(<AuthGate><Who /></AuthGate>);
    await userEvent.type(await screen.findByLabelText("E-mail"), "asha@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "nope-nope");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText(/Wrong e-mail or password/)).toBeInTheDocument();
    expect(screen.getByLabelText("E-mail")).toHaveValue("asha@example.com");
    expect(screen.queryByText(/App for/)).toBeNull();
  });

  it("first start: creates the owner account (passwords must match)", async () => {
    mock({ auth_enabled: true, setup_needed: true, user: null }, { status: 201, body: { user: { user_id: "o", name: "Vrushank", role: "owner" } } });
    render(<AuthGate><Who /></AuthGate>);
    await userEvent.type(await screen.findByLabelText("Your name"), "Vrushank");
    await userEvent.type(screen.getByLabelText(/E-mail/), "v@example.com");
    await userEvent.type(screen.getByLabelText(/^Password \(at least/), "long-password");
    await userEvent.type(screen.getByLabelText("Password again"), "other-password");
    await userEvent.click(screen.getByRole("button", { name: "Create my account" }));
    expect(await screen.findByText(/not the same/)).toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText("Password again"));
    await userEvent.type(screen.getByLabelText("Password again"), "long-password");
    await userEvent.click(screen.getByRole("button", { name: "Create my account" }));
    expect(await screen.findByText("App for Vrushank")).toBeInTheDocument();
  });

  it("goes straight to the app when sign-in is switched off", async () => {
    mock({ auth_enabled: false, setup_needed: false, user: null });
    render(<AuthGate><Who /></AuthGate>);
    expect(await screen.findByText("App for nobody")).toBeInTheDocument();
  });
});
