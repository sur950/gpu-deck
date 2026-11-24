import { redirect } from 'next/navigation';

/** /billing/reports (breadcrumb target) → the Reports tab. */
export default function ReportsIndex() {
  redirect('/billing?tab=reports');
}
