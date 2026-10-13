// Display helpers. Amounts arrive as decimal strings and are never turned into floats for
// arithmetic; they are only grouped for reading.

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "–";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("en-GB", {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** "7559.47" -> "7,559.47". Anything that is not a plain decimal is returned unchanged. */
export function formatAmount(value: string | null | undefined, currency?: string | null): string {
  if (value === null || value === undefined || value === "") return "–";
  const match = /^(-?)(\d+)(\.\d+)?$/.exec(value.trim());
  if (!match) return value;
  const grouped = match[2].replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const amount = `${match[1]}${grouped}${match[3] ?? ""}`;
  return currency ? `${amount} ${currency}` : amount;
}

/** "0.02" -> "2%". */
export function formatPercent(fraction: string | null | undefined): string {
  if (!fraction) return "–";
  const match = /^(\d+)(?:\.(\d+))?$/.exec(fraction.trim());
  if (!match) return fraction;
  const decimals = (match[2] ?? "").padEnd(2, "0");
  const whole = `${match[1]}${decimals.slice(0, 2)}`.replace(/^0+(?=\d)/, "");
  const rest = decimals.slice(2).replace(/0+$/, "");
  return `${whole}${rest ? `.${rest}` : ""}%`;
}

export function fileSizeLabel(megabytes: number): string {
  return `${megabytes} MB`;
}

export function extensionsLabel(extensions: string[]): string {
  const names = Array.from(new Set(extensions.map((ext) => ext.replace(/^\./, "").toUpperCase())));
  return names.map((name) => (name === "JPEG" ? "JPG" : name)).filter((n, i, all) => all.indexOf(n) === i).join(", ");
}

/** "Example Trading S.A. (SUP-001)": the master's legal name with its code. */
export function supplierLabel(name: string | null | undefined, id: string | null | undefined): string {
  if (name && id) return `${name} (${id})`;
  return name || id || "–";
}

/** 2411 -> "2.4 KB", 9437188 -> "9.0 MB". */
export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
