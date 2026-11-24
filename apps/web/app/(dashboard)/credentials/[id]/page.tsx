import type { Metadata } from 'next';
import { CredentialDetail } from '@/app/(dashboard)/credentials/_components/credential-detail';

export const metadata: Metadata = { title: 'Credential' };

export default function CredentialDetailRoute() {
  return <CredentialDetail />;
}
