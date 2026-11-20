# Wallpaper Parallax for i3

Обои плавно прокручиваются по горизонтали при смене рабочего стола i3, как домашний экран старого Android. Каждый рабочий стол показывает свой участок одной панорамы.

![Переключение рабочих столов в i3 с параллаксом обоев](docs/demo.gif)

[Скриншот в полном размере](docs/demo.png) · [Панорама из демонстрации](docs/panorama.jpg)

## Быстрый запуск

В открытой сессии i3 на X11:

```sh
nix run github:Kostr2007/Wallpaper-Parallax-i3/v0.1.0 -- --image "$HOME/Pictures/wallpaper.jpg"
```

Или установите команду из исходников в виртуальное окружение:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install .
wallpaper-parallax --image "$HOME/Pictures/wallpaper.jpg"
```

Для автоматического запуска добавьте в конфиг i3, указав свои абсолютные пути:

```i3
exec --no-startup-id /path/to/wallpaper-parallax --image /path/to/wallpaper.jpg
```

## Настройка

| Параметр | По умолчанию | Что меняет |
| --- | --- | --- |
| `--pages` | 5 | Сколько столов с номерами 1…N распределено по ширине панорамы |
| `--travel` | 20 | Полный ход камеры в процентах от ширины монитора |
| `--duration` | 320 | Длительность перехода в миллисекундах. При `0` положение меняется сразу |
| `--fps` | 60 | Верхняя граница частоты кадров |
| `--workspace NAME` | — | Имена столов по порядку, один флаг на имя. Используется вместо `--pages` |

Например, десять рабочих столов с ходом 36% и переходом за 340 мс:

```sh
wallpaper-parallax --image "$HOME/Pictures/wallpaper.jpg" --pages 10 --travel 36 --duration 340
```

Столы с номерами выше `--pages` показывают правый край. Столы без номера показывают центр, если их порядок не задан через `--workspace`. Картинка масштабируется до размера экрана с запасом на прокрутку. Чем больше `--travel`, тем заметнее эффект и тем сильнее приближение у картинки с пропорциями экрана.

### Подключение к менеджеру обоев

Вместо `--image` передайте `--state-file /path/to/wallpaper.json`. Менеджер обоев может обновлять файл атомарной заменой:

```json
{"image": "/home/alex/Pictures/wallpaper.jpg", "fit": "fill"}
```

`image` — абсолютный путь. `fit` по умолчанию равен `fill`. При значениях `center`, `max` и `tile` служба освобождает фон для основного менеджера обоев. Изменения файла подхватываются в течение примерно полусекунды.

### Home Manager

Добавьте в inputs своего flake:

```nix
wallpaper-parallax = {
  url = "github:Kostr2007/Wallpaper-Parallax-i3/v0.1.0";
  inputs.nixpkgs.follows = "nixpkgs";
};
```

В конфигурации пользователя Home Manager:

```nix
{
  imports = [ inputs.wallpaper-parallax.homeManagerModules.default ];
  services.wallpaper-parallax = {
    enable = true;
    image = "/home/alex/Pictures/wallpaper.jpg";
    pages = 5;
    travel = 20;
  };
}
```

Служба по умолчанию запускается через `i3-session.target`. Для Xsession, которой управляет Home Manager, задайте `sessionTarget = "graphical-session.target";`. В качестве источника можно задать `stateFile` вместо `image`.

## Совместимость и проверка

- Требуется i3 на X11 с 24-битным TrueColor. Поддерживаются PNG, JPEG, WebP и другие форматы Pillow. Для JPEG XL используется `djxl` из libjxl, который включён в пакет Nix.
- Изображение загружается в X11 при смене картинки или размера экрана. При переключении столов служба сдвигает область отрисовки в X-сервере. У каждого выхода своя позиция.
- Поддерживается picom. Служба рисует на прозрачном для ввода слое под окнами, поэтому после её остановки видны статичные обои X11.
- `nix flake check` запускает i3 на Xvfb и проверяет промежуточные кадры, два монитора, быструю смену направления, замену обоев и остановку.

Демонстрацию можно переснять командой `tools/record-demo.sh docs/panorama.jpg docs`. Нужны Xvfb, i3, picom, xwininfo, ffmpeg с `x11grab` и установленная команда `wallpaper-parallax`.

[Android WallpaperService.Engine](https://developer.android.com/reference/android/service/wallpaper/WallpaperService.Engine) · [Протокол i3 IPC](https://i3wm.org/docs/ipc.html)
