'use client';

import { useRouter } from 'next/navigation';
import { useState } from 'react';

import { createBrowserClient } from '@/lib/supabase/client';

export function SignOutButton() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);

  async function onClick() {
    setLoading(true);
    const supabase = createBrowserClient();
    await supabase.auth.signOut();
    router.push('/login');
    router.refresh();
  }

  return (
    <button
      onClick={onClick}
      disabled={loading}
      className="text-xs font-medium text-on-surface-variant transition-colors hover:text-primary disabled:opacity-50"
    >
      {loading ? 'Uscita…' : 'Esci'}
    </button>
  );
}
