"use client";

import { useEffect, useState } from "react";
import { ErrorNotice, Loading } from "../Feedback";
import { useApi } from "../Providers";

/**
 * The original document, shown by the browser's own PDF and image viewers.
 * It is fetched through the API client (the endpoint needs the caller's identity, which a
 * plain <iframe src> or <img src> cannot send) and displayed from a blob URL.
 *
 * The document is not cached (DL-80): it is requested every time the invoice is opened
 * and its blob URL is released when the view closes, so the file does not stay in memory.
 */
export function DocumentViewer({ invoiceId, filename }: { invoiceId: number; filename: string }) {
  const api = useApi();
  const [url, setUrl] = useState<string | null>(null);
  const [type, setType] = useState("");
  const [error, setError] = useState<unknown>(undefined);

  useEffect(() => {
    let cancelled = false;
    let created: string | null = null;
    setUrl(null);
    setError(undefined);
    api
      .getDocument(invoiceId)
      .then((blob) => {
        if (cancelled) return;
        created = URL.createObjectURL(blob);
        setType(blob.type);
        setUrl(created);
      })
      .catch((caught) => {
        if (!cancelled) setError(caught);
      });
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [api, invoiceId]);

  if (error !== undefined) return <ErrorNotice error={error} />;
  if (!url) return <Loading what="Loading the document" />;

  return (
    <div className="document">
      {type.startsWith("image/") ? (
        // eslint-disable-next-line @next/next/no-img-element -- a blob URL, not an optimisable asset
        <img src={url} alt={`Original document ${filename}`} />
      ) : (
        <iframe src={url} title={`Original document ${filename}`} />
      )}
      <p className="hint">
        <a href={url} target="_blank" rel="noreferrer">
          Open {filename} in a new tab
        </a>
      </p>
    </div>
  );
}
