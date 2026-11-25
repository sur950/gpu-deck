/**
 * Shared Delete Confirmation Dialog
 * Reusable confirmation dialog for delete actions across all modules
 */

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogMedia,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { AlertTriangle } from 'lucide-react';

interface DeleteConfirmationDialogProps {
  /**
   * Whether the dialog is open
   */
  open: boolean;
  /**
   * Called when the dialog open state changes
   */
  onOpenChange: (open: boolean) => void;
  /**
   * The name of the item being deleted (e.g., "production", "John Doe", "Admin Role")
   */
  itemName: string;
  /**
   * The type of resource being deleted (e.g., "tag", "user", "role", "layout")
   */
  resourceType: string;
  /**
   * Additional warning message (optional)
   * E.g., "This will remove it from 45 tagged resources."
   */
  warningMessage?: string;
  /**
   * Called when delete is confirmed
   */
  onConfirm: () => void | Promise<void>;
  /**
   * Whether the delete action is in progress
   */
  isDeleting?: boolean;
  /**
   * Custom confirm button text (default: "Delete {resourceType}")
   */
  confirmText?: string;
  /**
   * Override the dialog title (default: "Delete {resourceType}")
   */
  title?: string;
  /**
   * Override the dialog description (default: "Are you sure you want to delete…")
   */
  description?: string;
}

export function DeleteConfirmationDialog({
  open,
  onOpenChange,
  itemName,
  resourceType,
  warningMessage,
  onConfirm,
  isDeleting = false,
  confirmText,
  title,
  description,
}: DeleteConfirmationDialogProps) {
  const defaultTitle = `Delete ${resourceType.charAt(0).toUpperCase() + resourceType.slice(1)}`;
  const defaultConfirmText = `Delete ${resourceType.charAt(0).toUpperCase() + resourceType.slice(1)}`;

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogMedia>
            <AlertTriangle className="text-destructive" />
          </AlertDialogMedia>
          <AlertDialogTitle>{title ?? defaultTitle}</AlertDialogTitle>
            <AlertDialogDescription>
            {description ?? <>Are you sure you want to delete <b>&quot;{itemName}&quot;</b>?</>}
            {warningMessage && (
              <>
              <br /><br />
              {warningMessage}
              </>
            )}
            <br /><br />
            This action cannot be undone.
            </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={isDeleting}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={onConfirm}
            disabled={isDeleting}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
          >
            {isDeleting ? 'Deleting...' : (confirmText || defaultConfirmText)}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
