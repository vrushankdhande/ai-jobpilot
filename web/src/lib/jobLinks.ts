// Links to the job sites' OWN recommendation pages and searches, for YOU to open in your browser (signed in to your account).
// JobPilot never opens or reads these pages; it only builds the links.
import { linkedinSearchUrl } from "./linkedin";
import { naukriSearchUrl } from "./naukri";

export interface SiteLinks { site: string; recommended: string; search: string | null; note?: string }

export function jobSiteLinks(title: string, city: string): SiteLinks[] {
  const t = title.trim() || "AI Engineer", c = city.trim() || "Pune";
  const indeed = new URLSearchParams({ q: t, l: c, fromage: "7", sort: "date" });
  return [
    { site: "LinkedIn", recommended: "https://www.linkedin.com/jobs/collections/recommended/", search: linkedinSearchUrl({ keywords: t, location: c, postedWithin: "week", sortRecent: true }) },
    { site: "Naukri", recommended: "https://www.naukri.com/mnjuser/recommendedjobs", search: naukriSearchUrl({ keywords: t, location: c, postedWithin: "week" }) },
    { site: "Indeed", recommended: "https://in.indeed.com/", search: `https://in.indeed.com/jobs?${indeed}`, note: "'Jobs for you' on the home page when signed in" },
    { site: "Instahyre", recommended: "https://www.instahyre.com/candidate/opportunities/", search: null, note: "your matched opportunities after signing in" },
  ];
}
