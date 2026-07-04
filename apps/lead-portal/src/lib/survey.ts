import { API_URL } from '@/lib/api';

/** Submit the completed dossier survey (answers + the hot phone). */
export async function submitSurvey(
  slug: string,
  body: { answers: Record<string, string>; phone: string | null },
): Promise<void> {
  const res = await fetch(`${API_URL}/v1/public/lead/${encodeURIComponent(slug)}/survey`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error('Invio non riuscito. Riprova tra poco.');
  }
}

/** Fire-and-forget per-step progress ping so drop-off is visible in the timeline. */
export function trackSurveyStep(slug: string, step: number, total: number): void {
  const url = `${API_URL}/v1/public/lead/${encodeURIComponent(slug)}/survey/step?step=${step}&total=${total}`;
  void fetch(url, { method: 'POST', keepalive: true }).catch(() => undefined);
}
