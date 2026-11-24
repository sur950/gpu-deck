import type { Metadata } from 'next';
import { ReportDetail } from '@/app/(dashboard)/billing/_components/report-detail';

export const metadata: Metadata = { title: 'Billing report' };

export default function ReportDetailRoute() {
  return <ReportDetail />;
}
