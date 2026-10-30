import { Injectable } from '@angular/core';
import { Logger } from '@shared';

@Injectable({ providedIn: 'root' })
export class ApiService {
  constructor(private log: Logger) {}

  get<T>(url: string): Promise<T> {
    this.log.info(url);
    return fetch(url).then((r) => r.json());
  }
}
