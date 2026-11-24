"use client";

import * as React from "react";
import { X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

interface MultiSelectProps {
  options: { value: string; label: string }[];
  selected: string[];
  onChange: (selected: string[]) => void;
  placeholder?: string;
  className?: string;
}

export function MultiSelect({
  options,
  selected,
  onChange,
  placeholder = "Select items...",
  className,
}: MultiSelectProps) {
  const [open, setOpen] = React.useState(false);

  const handleSelect = (value: string | null) => {
    if (value && !selected.includes(value)) {
      onChange([...selected, value]);
    }
    setOpen(false);
  };

  const handleRemove = (value: string) => {
    onChange(selected.filter((item) => item !== value));
  };

  const availableOptions = options.filter(
    (option) => !selected.includes(option.value)
  );

  return (
    <div className={cn("space-y-2", className)}>
      {selected.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {selected.map((value) => {
            const option = options.find((opt) => opt.value === value);
            return (
              <Badge
                key={value}
                variant="secondary"
                className="pl-2 pr-1 py-1"
              >
                {option?.label || value}
                <Button
                  variant="ghost"
                  size="icon-sm"
                  className="ml-1 h-4 w-4 p-0 hover:bg-transparent"
                  onClick={() => handleRemove(value)}
                >
                  <X className="h-3 w-3" />
                </Button>
              </Badge>
            );
          })}
        </div>
      )}
      <Select
        open={open}
        onOpenChange={setOpen}
        onValueChange={handleSelect}
        value=""
      >
        <SelectTrigger className="w-full">
          <SelectValue placeholder={placeholder} />
        </SelectTrigger>
        <SelectContent>
          {availableOptions.length === 0 ? (
            <div className="py-6 text-center text-sm text-muted-foreground">
              No more options available
            </div>
          ) : (
            availableOptions.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))
          )}
        </SelectContent>
      </Select>
    </div>
  );
}
