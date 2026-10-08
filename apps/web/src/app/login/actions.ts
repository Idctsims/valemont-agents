"use server";

import { redirect } from "next/navigation";

import { isOwner } from "@/lib/owner";
import { safeNext } from "@/lib/routes";
import { createClient } from "@/lib/supabase/server";

export type LoginState = { error: string | null };

// One message for every credential failure, so the form never reveals
// whether an email has an account.
const BAD_CREDENTIALS = "Email or password is incorrect.";

export async function login(_prev: LoginState, form: FormData): Promise<LoginState> {
  const email = String(form.get("email") ?? "").trim();
  const password = String(form.get("password") ?? "");
  if (!email || !password) return { error: "Enter your email and password." };

  const supabase = await createClient();
  const { data, error } = await supabase.auth.signInWithPassword({ email, password });

  if (error) {
    if (error.status === 429) {
      return { error: "Too many attempts. Wait a minute, then try again." };
    }
    return { error: BAD_CREDENTIALS };
  }

  if (!isOwner(data.user?.id)) {
    await supabase.auth.signOut({ scope: "local" });
    return { error: "Not authorized." };
  }

  redirect(safeNext(form.get("next")));
}

export async function logout(): Promise<void> {
  const supabase = await createClient();
  await supabase.auth.signOut({ scope: "local" });
  redirect("/login");
}
