"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { Loading } from "@/components/Feedback";
import { useSession } from "@/lib/auth";
import { homeFor } from "@/lib/invoices/actions";

/** Sends each role to its starting page, or to the login. */
export default function Home() {
  const session = useSession();
  const router = useRouter();
  useEffect(() => {
    if (session === undefined) return;
    router.replace(session ? homeFor(session.role) : "/login");
  }, [session, router]);
  return <Loading />;
}
