import hashlib
import time
from typing import Optional 

class ResponseCache:
    """
    In-memory response cache with TTL (time-to-live).

    In production, replace this with Redis for:
    - Persistence across restarts
    - Shared cache across multiple instances
    - Built-in TTL management

    """

    def __init__(self, ttl_seconds: int = 300):
        self.ttl = ttl_seconds
        self._cache: dict[str, dict] = {}
        self._hits = 0
        self._misses = 0
    
    def make_key(self, query: str) -> str:
        """Create cache key from the normalized query."""
        normalized = query.lower().strip()
        return hashlib.sha256(normalized.encode()).hexdigest()

        """ 'Qual multa aplicada para o cliente X no contrato Y' and 'qUaL mUlTa aPlIcAdA pArA o ClIeNtE x No CoNtrAtO y' deve gerar a mesma chave
         mesmo se a API quiser verificar a chave, ou seja, independentemente de maiúsculas ou minúsculas
         e espaços em branco 
        """
    
    def get(self, query: str) -> Optional[dict]:
        """Get cached response for query if it exists and hasn't expired.
            Returns None on cache miss.
        """
        key = self.make_key(query)

        if key in self._cache:
            entry = self._cache[key]
            
            if time.time() - entry["timestamp"] < self.ttl:
                self._hits += 1
                return entry["response"]
            else:
                del self._cache[key] 
        
        self._misses += 1
        return None

    def set(self, query: str, response: str) -> None:
        """Store a response in the cache."""
        key = self.make_key(query)
        self._cache[key] = {
            "response": response,
            "timestamp": time.time(),
            "query": query
        }
    
    @property
    def stats(self) -> dict:
        """Return cache statistics."""
        total = self._hits + self._misses
        hit_rate = (self._hits / total) if total > 0 else 0.0
        return {
            "hits": self._hits,
            "misses": self._misses,
            "total": total,
            "hit_rate": hit_rate,
        }

    def clear(self) -> None:
        """Clear the cache."""
        self._cache.clear()
        self._hits = 0
        self._misses = 0
