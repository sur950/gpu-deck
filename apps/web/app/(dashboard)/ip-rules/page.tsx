import type { Metadata } from 'next';
import { IpRulesPage } from '@/app/(dashboard)/ip-rules/_components/ip-rules-page';

export const metadata: Metadata = { title: 'IP rules' };

export default function IpRulesRoute() {
  return <IpRulesPage />;
}
