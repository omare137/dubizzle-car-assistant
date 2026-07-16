import { Button } from "@/components/ui/button";
import { CalendarCheck } from "lucide-react";
import type { BookingPromptData } from "@/lib/api";

interface Props {
  data: BookingPromptData;
  onRespond: (message: string) => void;
}

export function BookingPrompt({ data, onRespond }: Props) {
  return (
    <div className="mt-3 flex flex-col gap-3 rounded-2xl border border-primary/40 bg-primary/5 p-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex items-start gap-3">
        <div className="rounded-full bg-primary/15 p-2 text-primary">
          <CalendarCheck className="h-5 w-5" />
        </div>
        <div>
          <p className="text-sm font-medium text-foreground">Confirm viewing</p>
          <p className="text-sm text-muted-foreground">
            {data.day} at {data.time}?
          </p>
        </div>
      </div>
      <div className="flex gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => onRespond("No, not that slot")}
        >
          No
        </Button>
        <Button size="sm" onClick={() => onRespond("Yes, please confirm the booking")}>
          Yes
        </Button>
      </div>
    </div>
  );
}
