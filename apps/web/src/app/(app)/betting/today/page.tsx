import { redirect } from "next/navigation";

// Today became the Parlays tab (Phase 4.5): an old link lands there.
export default function TodayPage() {
  redirect("/betting/parlays");
}
