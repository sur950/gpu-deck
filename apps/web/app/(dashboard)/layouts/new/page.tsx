import type { Metadata } from 'next';
import { LayoutEditor } from '@/app/(dashboard)/layouts/_components/layout-editor';

export const metadata: Metadata = { title: 'New layout' };

export default function NewLayoutPage() {
  return <LayoutEditor />;
}
