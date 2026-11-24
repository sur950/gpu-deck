import type { Metadata } from 'next';
import { LayoutEditor } from '@/app/(dashboard)/layouts/_components/layout-editor';

export const metadata: Metadata = { title: 'Layout details' };

export default function LayoutDetailPage() {
  return <LayoutEditor />;
}
