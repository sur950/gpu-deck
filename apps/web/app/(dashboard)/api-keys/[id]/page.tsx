import type { Metadata } from 'next';
import { ApiKeyDetail } from '@/app/(dashboard)/api-keys/_components/api-key-detail';

export const metadata: Metadata = { title: 'API key details' };

export default function ApiKeyDetailPage() {
  return <ApiKeyDetail />;
}
