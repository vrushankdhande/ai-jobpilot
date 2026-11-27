import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ListedSkillsCell } from "./ListedSkills";

afterEach(() => cleanup());

describe("ListedSkillsCell", () => {
  it("summarises what you have and lists have / related / missing / not checked", () => {
    render(<ListedSkillsCell check={{ skills: ["Python", "LangChain", "Kubernetes", "C++", "Agentic Workflows"], have: ["Python", "LangChain"],
      related: [{ skill: "Kubernetes", via: "Docker" }], missing: ["C++"], not_checked: ["Agentic Workflows"], checked: 4 }} />);
    expect(screen.getByText("2 you have · 1 related · 1 missing · 1 not checked")).toBeInTheDocument();
    expect(screen.getByText("✅ Python, LangChain")).toBeInTheDocument();
    expect(screen.getByText("≈ Kubernetes (you have Docker)")).toBeInTheDocument();
    expect(screen.getByText("❌ C++")).toBeInTheDocument();
    expect(screen.getByText(/Not checked: Agentic Workflows/)).toBeInTheDocument();
    expect(screen.queryByText(/%/)).toBeNull();                                     // never shown as a score
  });
  it("shows a dash for jobs without listed skills", () => {
    render(<ListedSkillsCell check={undefined} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
