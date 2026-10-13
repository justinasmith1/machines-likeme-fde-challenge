"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import type { Role } from "@/lib/api/types";
import { auth, isDevelopmentLogin, useSession, type Session } from "@/lib/auth";
import { ROLE_LABELS, navigationFor } from "@/lib/invoices/actions";
import { Loading } from "./Feedback";

interface AppShellProps {
  title: string;
  /** Roles this page is for. Others get an explanation instead of the content. */
  roles?: Role[];
  /**
   * Which navigation item to mark as the current one. Left out, it is the item whose
   * address the page is under. A page reached from more than one place passes the place
   * the person came from, or null to mark none.
   */
  activeHref?: string | null;
  children: (session: Session) => ReactNode;
}

/**
 * Frame of every signed-in page: navigation for the role, who is signed in, sign out.
 * It sends people without a session to the login. It also keeps a role out of pages that
 * are not for it, but that is only tidiness: the API is what enforces permissions.
 */
export function AppShell({ title, roles, activeHref, children }: AppShellProps) {
  const session = useSession();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (session === null) router.replace("/login");
  }, [session, router]);

  if (!session) return <Loading what="Checking your session" />;

  const allowed = !roles || roles.includes(session.role);
  const navigation = navigationFor(session.role);
  const active =
    activeHref !== undefined ? activeHref : navigation.find((item) => pathname?.startsWith(item.href))?.href;
  return (
    <>
      <header className="topbar">
        <span className="brand">
          <span className="brand-mark" aria-hidden="true">
            ◆
          </span>
          Contoso <span className="brand-product">Invoice Matching</span>
        </span>
        <nav aria-label="Main">
          {navigation.map((item) => (
            <Link key={item.href} href={item.href} aria-current={item.href === active ? "page" : undefined}>
              {item.label}
            </Link>
          ))}
        </nav>
        <span className="who">
          <span className="who-name">
            {session.username} · {ROLE_LABELS[session.role]}
          </span>
          <button
            type="button"
            className="link"
            onClick={() => {
              auth.signOut();
              router.replace("/login");
            }}
          >
            Sign out
          </button>
        </span>
      </header>
      {isDevelopmentLogin && (
        <p className="devbar" role="note">
          Development login. Identity is not verified; do not use with real data outside a local machine.
        </p>
      )}
      <main>
        <h1>{title}</h1>
        {allowed ? (
          children(session)
        ) : (
          <div className="notice notice-warning" role="alert">
            <strong>Not available for your role.</strong> This page is for{" "}
            {roles!.map((role) => ROLE_LABELS[role]).join(" and ")}. You are signed in as{" "}
            {ROLE_LABELS[session.role]}.
          </div>
        )}
      </main>
    </>
  );
}
