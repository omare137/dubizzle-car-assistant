import { useState } from "react";
import type { ApiCar } from "@/lib/api";
import { titleCase, formatPriceAED } from "@/lib/format";
import { Button } from "@/components/ui/button";
import { Car } from "lucide-react";

interface Props {
  car: ApiCar;
  onBook: (car: ApiCar) => void;
}

export function CarCard({ car, onBook }: Props) {
  const [imgFailed, setImgFailed] = useState(false);
  const subtitle = `${car.year} ${titleCase(car.make)} ${titleCase(car.model)}`;
  // Display priority per API contract: cash > monthly > "Price on request".
  const priceLabel =
    car.price_aed_cash != null
      ? formatPriceAED(car.price_aed_cash)
      : car.price_aed_monthly != null
        ? `${formatPriceAED(car.price_aed_monthly)}/month`
        : "Price on request";

  return (
    <div className="flex w-72 shrink-0 flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-sm transition-shadow hover:shadow-md">
      <div className="relative aspect-[4/3] w-full overflow-hidden bg-muted">
        {imgFailed ? (
          <div className="flex h-full w-full items-center justify-center text-muted-foreground">
            <Car className="h-10 w-10" aria-hidden />
          </div>
        ) : (
          <img
            src={car.photo_url}
            alt={car.title}
            loading="lazy"
            onError={() => setImgFailed(true)}
            className="h-full w-full object-cover"
          />
        )}
      </div>
      <div className="flex flex-1 flex-col gap-2 p-4">
        <h3 className="line-clamp-2 text-base font-semibold leading-tight text-foreground">
          {car.title}
        </h3>
        <p className="text-sm text-muted-foreground">{subtitle}</p>
        <div className="mt-auto flex items-center justify-between pt-3">
          <span className="text-lg font-bold text-primary">{priceLabel}</span>
        </div>
        <Button
          onClick={() => onBook(car)}
          className="mt-1 w-full"
          size="sm"
        >
          Book a viewing
        </Button>
      </div>
    </div>
  );
}
