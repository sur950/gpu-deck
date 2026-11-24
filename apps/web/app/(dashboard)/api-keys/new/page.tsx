import type { Metadata } from 'next';
import { ApiKeyNew } from '@/app/(dashboard)/api-keys/_components/api-key-new';

export const metadata: Metadata = { title: 'New API key' };

export default function NewApiKeyPage() {
  return <ApiKeyNew />;
}
