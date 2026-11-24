import type { Metadata } from 'next';
import { RuleEdit } from '@/app/(dashboard)/rules/_components/rule-edit';

export const metadata: Metadata = { title: 'Edit rule' };

export default function EditRulePage() {
  return <RuleEdit />;
}
