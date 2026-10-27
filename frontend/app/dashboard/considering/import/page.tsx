'use client';
import { useSearchParams } from 'next/navigation';
import { ConsideringImport } from '@/components/considering-import';
import { ConsideringFallback } from '@/components/considering-fallback';
export default function ImportPage() {
  const params = useSearchParams();
  return params.get('draft') ? <ConsideringImport /> : <ConsideringFallback />;
}
