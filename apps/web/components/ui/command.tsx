"use client"

import * as React from "react"
import { Search } from "lucide-react"
import { cn } from "@/lib/utils"

interface CommandContextValue {
  search: string;
  setSearch: (search: string) => void;
  visibleCount: number;
  reportVisibility: (id: string, visible: boolean) => void;
}

const CommandContext = React.createContext<CommandContextValue>({
  search: "",
  setSearch: () => {},
  visibleCount: 0,
  reportVisibility: () => {},
});

const Command = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, children, ...props }, ref) => {
  const [search, setSearch] = React.useState("");
  const [visibleCount, setVisibleCount] = React.useState(0);
  const visibleItemsRef = React.useRef<Set<string>>(new Set());

  const reportVisibility = React.useCallback((id: string, visible: boolean) => {
    const wasVisible = visibleItemsRef.current.has(id);
    if (visible === wasVisible) return;
    if (visible) {
      visibleItemsRef.current.add(id);
    } else {
      visibleItemsRef.current.delete(id);
    }
    setVisibleCount(visibleItemsRef.current.size);
  }, []);

  return (
    <CommandContext.Provider value={{ search, setSearch, visibleCount, reportVisibility }}>
      <div
        ref={ref}
        className={cn(
          "flex h-full w-full flex-col overflow-hidden rounded-md bg-popover text-popover-foreground",
          className
        )}
        {...props}
      >
        {children}
      </div>
    </CommandContext.Provider>
  );
});
Command.displayName = "Command";

const CommandInput = React.forwardRef<
  HTMLInputElement,
  React.InputHTMLAttributes<HTMLInputElement>
>(({ className, onChange, ...props }, ref) => {
  const { setSearch } = React.useContext(CommandContext);

  return (
    <div className="flex items-center border-b px-3">
      <Search className="mr-2 h-4 w-4 shrink-0 opacity-50" />
      <input
        ref={ref}
        className={cn(
          "flex h-10 w-full rounded-md bg-transparent py-3 text-sm outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50",
          className
        )}
        onChange={(e) => {
          setSearch(e.target.value);
          onChange?.(e);
        }}
        {...props}
      />
    </div>
  );
});
CommandInput.displayName = "CommandInput";

const CommandList = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, children, ...props }, ref) => (
  <div
    ref={ref}
    className={cn("max-h-[300px] overflow-y-auto overflow-x-hidden", className)}
    {...props}
  >
    {children}
  </div>
));
CommandList.displayName = "CommandList";

const CommandEmpty = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, children, ...props }, ref) => {
  const { search, visibleCount } = React.useContext(CommandContext);
  if (!search || visibleCount > 0) return null;
  return (
    <div
      ref={ref}
      className={cn("py-6 text-center text-sm", className)}
      {...props}
    >
      {children}
    </div>
  );
});
CommandEmpty.displayName = "CommandEmpty";

const CommandGroup = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn("overflow-hidden p-1 text-foreground", className)}
    {...props}
  />
));
CommandGroup.displayName = "CommandGroup";

const CommandItem = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement> & { onSelect?: () => void; value?: string }
>(({ className, onSelect, value, children, ...props }, ref) => {
  const { search, reportVisibility } = React.useContext(CommandContext);
  const id = React.useRef(Math.random().toString(36).slice(2)).current;

  const isVisible =
    !search || !value || value.toLowerCase().includes(search.toLowerCase());

  React.useLayoutEffect(() => {
    reportVisibility(id, isVisible);
    return () => reportVisibility(id, false);
  }, [id, isVisible, reportVisibility]);

  if (!isVisible) return null;

  return (
    <div
      ref={ref}
      className={cn(
        "relative flex cursor-pointer select-none items-center rounded-sm px-2 py-1.5 text-sm outline-none hover:bg-accent hover:text-accent-foreground data-[disabled]:pointer-events-none data-[disabled]:opacity-50",
        className
      )}
      onClick={onSelect}
      {...props}
    >
      {children}
    </div>
  );
});
CommandItem.displayName = "CommandItem";

const CommandSeparator = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn("-mx-1 h-px bg-border", className)}
    {...props}
  />
));
CommandSeparator.displayName = "CommandSeparator";

export {
  Command,
  CommandInput,
  CommandList,
  CommandEmpty,
  CommandGroup,
  CommandItem,
  CommandSeparator,
}
