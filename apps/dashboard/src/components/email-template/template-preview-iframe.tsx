'use client';

/**
 * Template preview iframe — Sprint 9 Fase C.5.
 *
 * Displays a rendered email HTML inside a sandboxed iframe.
 * Accepts either:
 *   - `srcDoc`: raw HTML string (used for custom template preview)
 *   - `src`: URL to load (used when the backend serves the HTML)
 *
 * Mirrors the pattern used in BrandingEditor.
 */

import { useEffect, useRef, useState } from 'react';

interface TemplatePreviewIframeProps {
  /** Raw HTML to display via srcDoc — highest priority. */
  srcDoc?: string;
  /** URL to load in the iframe — used when srcDoc is not provided. */
  src?: string;
  /** Height of the iframe (default: 600). */
  height?: number;
  className?: string;
}

export function TemplatePreviewIframe({
  srcDoc,
  src,
  height = 600,
  className = '',
}: TemplatePreviewIframeProps) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
  }, [srcDoc, src]);

  if (!srcDoc && !src) {
    return (
      <div
        className={`flex items-center justify-center rounded-xl border bg-surface-variant/30 text-sm text-on-surface-variant ${className}`}
        style={{ height }}
      >
        Nessuna anteprima disponibile
      </div>
    );
  }

  return (
    <div className={`relative rounded-xl border overflow-hidden ${className}`} style={{ height }}>
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center bg-surface/70 z-10">
          <span className="text-sm text-on-surface-variant animate-pulse">
            Caricamento anteprima…
          </span>
        </div>
      )}
      <iframe
        ref={iframeRef}
        srcDoc={srcDoc}
        src={!srcDoc ? src : undefined}
        sandbox="allow-same-origin allow-popups"
        className="w-full h-full border-0"
        style={{ height }}
        onLoad={() => setLoading(false)}
        title="Anteprima email"
      />
    </div>
  );
}
