// API failures as one error type with the backend's stable code, and the text shown to
// the user for each code. Screens never show raw responses or stack traces.

export type ApiErrorCode =
  | "UNAUTHENTICATED"
  | "PERMISSION_DENIED"
  | "NOT_FOUND"
  | "INVALID_STATE"
  | "FILE_TOO_LARGE"
  | "VALIDATION_ERROR"
  | "UNSUPPORTED_DOCUMENT_TYPE"
  // Answered by the frontend's own /api proxy when the backend cannot be reached:
  | "BACKEND_UNAVAILABLE"
  // Raised by the client itself:
  | "NETWORK_ERROR"
  | "UNEXPECTED_ERROR";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

// Fixed text for codes where the cause is not something the backend explains to the user.
const FIXED_MESSAGES: Record<string, string> = {
  UNAUTHENTICATED: "You are not signed in, or your session is no longer valid. Sign in again.",
  NETWORK_ERROR: "The service cannot be reached. Check your connection and try again.",
  BACKEND_UNAVAILABLE: "The service is temporarily unavailable. Try again in a moment.",
  UNEXPECTED_ERROR: "Something went wrong. Try again; if it keeps happening, contact support.",
};

// For these codes the backend writes the message for the person using the system
// (for example which role is required, or why a decision does not apply), so it is shown.
const BACKEND_MESSAGE_CODES = new Set([
  "PERMISSION_DENIED",
  "NOT_FOUND",
  "INVALID_STATE",
  "FILE_TOO_LARGE",
  "VALIDATION_ERROR",
  "UNSUPPORTED_DOCUMENT_TYPE",
]);

const TITLES: Record<string, string> = {
  UNAUTHENTICATED: "Sign-in required",
  PERMISSION_DENIED: "Not allowed",
  NOT_FOUND: "Not found",
  INVALID_STATE: "This action no longer applies",
  FILE_TOO_LARGE: "File too large",
  VALIDATION_ERROR: "Check the information entered",
  UNSUPPORTED_DOCUMENT_TYPE: "File type not supported",
  NETWORK_ERROR: "Connection problem",
  BACKEND_UNAVAILABLE: "Service unavailable",
  UNEXPECTED_ERROR: "Unexpected error",
};

export interface ErrorDescription {
  code: string;
  title: string;
  message: string;
}

/** Text to show for any thrown value. Unknown errors get the generic message. */
export function describeError(error: unknown): ErrorDescription {
  if (error instanceof ApiError) {
    const known = error.code in TITLES;
    const code = known ? error.code : "UNEXPECTED_ERROR";
    const message =
      BACKEND_MESSAGE_CODES.has(code) && error.message
        ? error.message
        : (FIXED_MESSAGES[code] ?? FIXED_MESSAGES.UNEXPECTED_ERROR);
    return { code, title: TITLES[code], message };
  }
  return {
    code: "UNEXPECTED_ERROR",
    title: TITLES.UNEXPECTED_ERROR,
    message: FIXED_MESSAGES.UNEXPECTED_ERROR,
  };
}

export function isApiError(error: unknown, code?: ApiErrorCode): error is ApiError {
  return error instanceof ApiError && (code === undefined || error.code === code);
}
