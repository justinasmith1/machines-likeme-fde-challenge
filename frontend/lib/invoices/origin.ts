// Where the value of a field comes from: read by the model, or typed by a person in a
// correction. A corrected value must never look like something the model read.

import type { FieldOrigin, ReadStatus } from "@/lib/api/types";

export interface OriginText {
  corrected: boolean;
  /** "Read", "Illegible", ... or "Corrected by ana.analyst". */
  label: string;
  /** For a corrected field, what it was before: "was illegible", "was 3", "was empty". */
  was: string | null;
}

const STATUS_LABELS: Record<string, string> = {
  read: "Read",
  illegible: "Illegible",
  absent: "Not on the document",
};

function previously(origin: Extract<FieldOrigin, { source: "HUMAN_CORRECTION" }>): string {
  const value = origin.previous_value;
  const shown = Array.isArray(value) ? value.join(", ") : value === null || value === undefined ? "" : String(value);
  if (origin.previous_status === "illegible") return "was illegible";
  if (origin.previous_status === "absent") return "was not on the document";
  return shown === "" ? "was empty" : `was ${shown}`;
}

/** Text for the tag next to a field. `status` is the field's current reading status. */
export function describeOrigin(origin: FieldOrigin | null | undefined, status?: ReadStatus | null): OriginText {
  if (origin && origin.source === "HUMAN_CORRECTION") {
    return { corrected: true, label: `Corrected by ${origin.corrected_by}`, was: previously(origin) };
  }
  return { corrected: false, label: status ? (STATUS_LABELS[status] ?? status) : "", was: null };
}

export function isCorrected(origin: FieldOrigin | null | undefined): boolean {
  return origin?.source === "HUMAN_CORRECTION";
}
