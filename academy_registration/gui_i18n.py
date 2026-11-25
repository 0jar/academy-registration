import re
from string import Formatter

LANGUAGES = ("English", "Tiếng Việt")

VI = {
  "Language": "Ngôn ngữ",
  "Academy Registration": "Đăng ký học phần",
  "Open settings...": "Mở cài đặt...",
  "Save settings...": "Lưu cài đặt...",
  "Student ID": "Mã sinh viên",
  "Password": "Mật khẩu",
  "Semester": "Học kỳ",
  "Opening (Hanoi time)": "Giờ mở đăng ký (giờ Hà Nội)",
  "Refresh seconds": "Làm mới mỗi (giây)",
  "Window minutes": "Thời gian đăng ký (phút)",
  "Example opening: 2027-01-05 08:00 Hanoi": "Ví dụ giờ mở đăng ký: 2027-01-05 08:00 Hanoi",
  "Courses to register": "Học phần cần đăng ký",
  "Schedule filters": "Bộ lọc thời khóa biểu",
  "Fewest days": "Ít ngày đi học nhất",
  "Least fatigue": "Ít mệt nhất",
  "Include codes": "Chỉ gồm các mã",
  "Exclude codes": "Loại các mã",
  "Separate subject codes with spaces or commas.": "Ngăn cách các mã học phần bằng dấu cách hoặc dấu phẩy.",
  "Hide the school browser": "Ẩn trình duyệt khi mở cổng đăng ký",
  "Suggest schedule": "Gợi ý lịch học",
  "Schedule registration...": "Hẹn giờ đăng ký...",
  "Cancel run": "Hủy lượt chạy",
  "Timetable": "Thời khóa biểu",
  "Results": "Kết quả",
  "Run log": "Nhật ký",
  "Week of": "Tuần bắt đầu từ",
  "Session": "Ca học",
  "Monday": "Thứ Hai",
  "Tuesday": "Thứ Ba",
  "Wednesday": "Thứ Tư",
  "Thursday": "Thứ Năm",
  "Friday": "Thứ Sáu",
  "Saturday": "Thứ Bảy",
  "Periods {first}–{last}": "Tiết {first}–{last}",
  "Use suggested groups in settings": "Dùng các nhóm được gợi ý",
  "Subject / group": "Học phần / Nhóm",
  "Name": "Tên học phần",
  "Status": "Trạng thái",
  "Registered": "Đã đăng ký",
  "Already checked": "Đã tích chọn",
  "Suggested": "Được gợi ý",
  "Planned": "Dự kiến",
  "Enter your student ID and password to suggest a schedule, or open a settings file.": (
    "Nhập mã sinh viên và mật khẩu để xem lịch gợi ý, hoặc mở tệp cài đặt."
  ),
  "Suggest a schedule to see the timetable.": "Chọn Gợi ý lịch học để xem thời khóa biểu.",
  "Settings files": "Tệp cài đặt",
  "All files": "Tất cả tệp",
  "Couldn't open settings": "Không mở được cài đặt",
  "Opened {name}.": "Đã mở {name}.",
  "Check your settings": "Kiểm tra cài đặt",
  "Text settings": "Tệp cài đặt dạng văn bản",
  "Couldn't save settings": "Không lưu được cài đặt",
  "Choose a .txt filename.": "Chọn tên tệp có đuôi .txt.",
  "Saving failed ({error}).": "Không lưu được tệp ({error}).",
  "Saved {name}.": "Đã lưu {name}.",
  "Enter your student ID to fetch the course list.": "Nhập mã sinh viên để tải danh sách học phần.",
  "Enter your school password to start a run.": "Nhập mật khẩu tài khoản của trường để bắt đầu.",
  "Registration window ended": "Đã hết thời gian đăng ký",
  "Check the opening time in your settings.": "Kiểm tra giờ mở đăng ký trong cài đặt.",
  "Register {targets} when the window opens at {when} Hanoi time?": (
    "Đăng ký {targets} lúc {when} (giờ Hà Nội) khi cổng mở?"
  ),
  "The window is already open according to your settings. The tool may submit right away.": (
    "Theo cài đặt, đã đến giờ đăng ký. Ứng dụng có thể gửi đăng ký ngay."
  ),
  "Schedule registration": "Hẹn giờ đăng ký",
  "Keep this window open and your computer awake.": "Giữ cửa sổ này mở và đừng để máy tính chuyển sang chế độ ngủ.",
  "Signing in to fetch the course list...": "Đang đăng nhập để tải danh sách học phần...",
  "Opening the portal...": "Đang mở cổng đăng ký...",
  "Cancelling and closing the browser...": "Đang hủy lượt chạy và đóng trình duyệt...",
  "Close Academy Registration": "Đóng ứng dụng đăng ký học phần",
  "Cancel the active run and close this window?": "Hủy lượt chạy hiện tại và đóng cửa sổ này?",
  "Replace course choices": "Thay các học phần đã chọn",
  "Replace the course choices in the editor with the suggested groups?": (
    "Thay các học phần trong ô nhập bằng những nhóm được gợi ý?"
  ),
  "Suggested groups copied. Review them before scheduling registration.": (
    "Đã chép các nhóm được gợi ý. Kiểm tra lại trước khi hẹn giờ đăng ký."
  ),
  "Suggested groups copied. Enter the opening time before scheduling registration.": (
    "Đã chép các nhóm được gợi ý. Nhập giờ mở đăng ký trước khi hẹn giờ."
  ),
  "{days} school days · {gaps} skipped sessions · Longest stretch: {longest} periods · {blocks} blocks of three": (
    "{days} ngày đi học · {gaps} ca trống giữa buổi · Liền mạch dài nhất: {longest} tiết · {blocks} lượt học 3 tiết liền"
  ),
  "Search limit reached. Showing the best schedule found.": (
    "Đã đến giới hạn tìm kiếm. Đang hiển thị lịch tốt nhất tìm được."
  ),
  "Couldn't read: {groups}": "Không đọc được lịch của: {groups}",
  "{registered} registered · {planned} planned": "{registered} đã đăng ký · {planned} dự kiến",
  "Installing required Python packages. This may take a moment.": "Đang cài các gói Python cần thiết. Vui lòng đợi.",
  "Finding packages...": "Đang tìm các gói...",
  "Downloading packages...": "Đang tải các gói...",
  "Reading cached packages...": "Đang đọc các gói đã lưu...",
  "Preparing packages...": "Đang chuẩn bị các gói...",
  "Installing packages...": "Đang cài các gói...",
  "Couldn't check the Python packages.": "Không kiểm tra được các gói Python.",
  "Couldn't read the bundled requirements. Get a fresh copy of the app.": (
    "Không đọc được danh sách gói đi kèm. Hãy tải lại ứng dụng."
  ),
  "Couldn't install the required Python packages. Check your connection and pip setup, or launch the app from a virtual environment.": (
    "Không cài được các gói Python cần thiết. Kiểm tra kết nối và cấu hình pip, hoặc chạy ứng dụng trong môi trường ảo."
  ),
  "Python still can't find these required packages: {packages}": "Python vẫn không tìm thấy các gói: {packages}",
  "Registration verified.": "Đã xác nhận đăng ký.",
  "Schedule ready.": "Đã có lịch học gợi ý.",
  "Cancelled.": "Đã hủy.",
  "Cancelled. If a submission was in progress, check the portal before retrying.": (
    "Đã hủy. Nếu ứng dụng đang gửi đăng ký, hãy kiểm tra cổng đăng ký trước khi thử lại."
  ),
  "Stopped after {error}. Check the run folder for details.": "Đã dừng do lỗi {error}. Xem thư mục của lượt chạy để biết chi tiết.",
  "Stopped. {detail}": "Đã dừng. {detail}",
  "Saved run folder at {path}": "Đã lưu dữ liệu lượt chạy tại {path}",
  "Using {browser}.": "Đang dùng {browser}.",
  "Couldn't start {browser}. Trying the next browser.": "Không mở được {browser}. Đang thử trình duyệt tiếp theo.",
  "No supported browser detected. Downloading Playwright Chromium...": (
    "Không tìm thấy trình duyệt được hỗ trợ. Đang tải Chromium của Playwright..."
  ),
  "Signed in. Your registration page is open.": "Đã đăng nhập và mở trang đăng ký học phần.",
  "Ready to go at {when} (Hanoi time).": "Sẵn sàng đăng ký lúc {when} (giờ Hà Nội).",
  "Session checked. Ready for registration.": "Đã kiểm tra phiên đăng nhập. Sẵn sàng đăng ký.",
  "Done. All {count} courses are on the refreshed registration list.": (
    "Đã xong. Cả {count} học phần đều có trong danh sách đăng ký sau khi làm mới."
  ),
  "Registering {course}": "Đang đăng ký {course}",
  "Confirmed {course} on the registered list.": "Đã xác nhận {course} trong danh sách đăng ký.",
  "No seat for {course}. Checking which fallbacks fit.": "{course} đã hết chỗ. Đang tìm nhóm dự phòng phù hợp.",
  "Last thing the browser was doing: {stage}": "Bước gần nhất của trình duyệt: {stage}",
  "Preferences line {line}: {detail}": "Dòng {line} trong cài đặt: {detail}",
  "Check your settings. {detail}": "Kiểm tra cài đặt. {detail}",
  "Put the value after the label and colon. Blank lines and # comments are fine.": (
    "Nhập giá trị sau nhãn và dấu hai chấm. Có thể để dòng trống hoặc ghi chú bằng #."
  ),
  "Enter the opening time like 2027-01-05 08:00 Hanoi. An ISO time with an offset works too.": (
    "Nhập giờ mở đăng ký theo mẫu 2027-01-05 08:00 Hanoi, hoặc dùng giờ ISO có độ lệch múi giờ."
  ),
  "Use whole seconds for Refresh and whole minutes for Window.": "Nhập số nguyên cho Refresh (giây) và Window (phút).",
  "Enter the semester shown on the portal.": "Nhập học kỳ hiển thị trên cổng đăng ký.",
  "Add at least one course.": "Thêm ít nhất một học phần.",
  "Another run is already using this account.": "Tài khoản này đang được dùng trong một lượt chạy khác.",
  "The semester on the portal doesn't match your settings.": "Học kỳ trên cổng đăng ký khác với học kỳ trong cài đặt.",
  "The portal is asking for a CAPTCHA or one-time code. Finish that in the browser.": (
    "Cổng đăng ký yêu cầu CAPTCHA hoặc mã dùng một lần. Hãy hoàn tất bước đó trong trình duyệt."
  ),
  "The portal says registration is closed.": "Cổng đăng ký báo đã đóng đăng ký học phần.",
  "An earlier submission is still unconfirmed. Check the portal first.": (
    "Một lượt gửi trước đó vẫn chưa được xác nhận. Hãy kiểm tra cổng đăng ký trước."
  ),
  "Enter a day and its periods, like Mon 7-9, Tue 7-9.": "Nhập thứ và tiết học theo mẫu Mon 7-9, Tue 7-9.",
  "This setting is listed twice.": "Mục cài đặt này bị lặp.",
  "Add a Course line before listing its groups.": "Thêm dòng Course trước khi liệt kê các nhóm.",
  "Use Group: 2 | Friday 10-12.": "Nhập theo mẫu Group: 2 | Friday 10-12.",
  "Unknown label. Use Student, Semester, Opens, Refresh, Window, Course, Group, Fallback, or Last resort.": (
    "Nhãn không hợp lệ. Dùng Student, Semester, Opens, Refresh, Window, Course, Group, Fallback hoặc Last resort."
  ),
  "One of these settings doesn't fit the text format. Save this plan as JSON.": (
    "Có mục cài đặt không lưu được ở dạng văn bản. Hãy lưu kế hoạch ở dạng JSON."
  ),
  "Enter a group without slashes or control characters.": "Nhập nhóm học phần không chứa dấu gạch chéo hay ký tự điều khiển.",
  "last_resort must be true or false.": "last_resort phải là true hoặc false.",
  "Pick one schedule format for this group, either slots or days with periods.": (
    "Chọn một cách ghi lịch cho nhóm này: slots hoặc days kèm periods."
  ),
  "days must be a list of unique weekdays.": "days phải là danh sách các thứ không trùng nhau.",
  "Add at least one class time for each group.": "Thêm ít nhất một giờ học cho mỗi nhóm.",
  "Use weekdays 2 (Monday) through 7 (Saturday).": "Dùng các thứ từ 2 (thứ Hai) đến 7 (thứ Bảy).",
  "Enter the first and last period in order, starting at 1 or higher.": (
    "Nhập tiết đầu và tiết cuối theo thứ tự, với tiết đầu từ 1 trở lên."
  ),
  "That class time is listed twice.": "Giờ học này bị lặp.",
  "Put your JSON settings inside a single object.": "Đặt tất cả cài đặt JSON trong một đối tượng.",
  "Leave your password out of the JSON file. Use the hidden prompt or ACADEMY_PASSWORD.": (
    "Không ghi mật khẩu vào tệp JSON. Nhập trong ô mật khẩu hoặc dùng ACADEMY_PASSWORD."
  ),
  "Enter your student ID without spaces around it or any slashes or control characters.": (
    "Nhập mã sinh viên, bỏ dấu cách ở đầu và cuối, dấu gạch chéo và ký tự điều khiển."
  ),
  "Opening time must include its UTC offset.": "Giờ mở đăng ký cần có độ lệch so với UTC.",
  "{field} must be an integer of at least {minimum}.": "{field} phải là số nguyên từ {minimum} trở lên.",
  "Enter course codes without slashes or control characters.": (
    "Nhập mã học phần không chứa dấu gạch chéo hay ký tự điều khiển."
  ),
  "List at least one group for each course, in the order you want them.": (
    "Liệt kê ít nhất một nhóm cho mỗi học phần, theo thứ tự ưu tiên."
  ),
  "A course has the same group listed twice.": "Có học phần bị lặp nhóm.",
  "The same course code is listed twice.": "Có mã học phần bị lặp.",
  "Check your settings. Look for missing fields, invalid values, or an opening date that doesn't parse.": (
    "Kiểm tra xem cài đặt có mục bị thiếu, giá trị không hợp lệ hoặc ngày mở đăng ký sai định dạng không."
  ),
  "Couldn't open your settings. Run --configure or point --config at a UTF-8 file.": (
    "Không mở được cài đặt. Hãy chọn một tệp UTF-8."
  ),
  "Check your settings. Fix the JSON syntax or use --configure to make a text file.": (
    "Kiểm tra cú pháp JSON hoặc tạo tệp cài đặt dạng văn bản."
  ),
  "Use a settings file ending in .txt or .json.": "Chọn tệp cài đặt có đuôi .txt hoặc .json.",
  "Multiple registered/pre-checked groups for {code}. Check the portal before continuing.": (
    "{code} có nhiều nhóm đã đăng ký hoặc đã tích chọn. Kiểm tra cổng đăng ký trước khi tiếp tục."
  ),
  "Couldn't check the timetable for {course}": "Không kiểm tra được lịch của {course}",
  "Existing registrations/pre-checked selections overlap: {courses}. Check the portal before continuing.": (
    "Các nhóm đã đăng ký hoặc đã tích chọn bị trùng lịch: {courses}. Kiểm tra cổng đăng ký trước khi tiếp tục."
  ),
  "Timetable changed for {course}": "Lịch của {course} đã thay đổi",
  "{course} overlaps with {other}, which is already selected.": "{course} trùng lịch với {other}, nhóm đã được chọn.",
  "Couldn't save everything. Check the final registration status.": (
    "Không lưu được toàn bộ kết quả. Hãy kiểm tra trạng thái đăng ký cuối cùng."
  ),
  "The registration window ended before I could confirm every course.": (
    "Đã hết thời gian đăng ký trước khi xác nhận được tất cả học phần."
  ),
  "Couldn't save the failure report.": "Không lưu được báo cáo lỗi.",
  "Couldn't load Playwright. Check the packages in your Python environment.": (
    "Không tải được Playwright. Kiểm tra các gói trong môi trường Python."
  ),
  "This registration window has ended. Check the opening time in your settings.": (
    "Đã hết thời gian đăng ký. Kiểm tra lại giờ mở đăng ký trong cài đặt."
  ),
  "The last submission is still unconfirmed. Check the portal before clearing the journal.": (
    "Lượt gửi gần nhất vẫn chưa được xác nhận. Kiểm tra cổng đăng ký trước khi xóa nhật ký."
  ),
  "The browser went somewhere unexpected. Stopped before entering your login details.": (
    "Trình duyệt đã chuyển sang trang khác dự kiến. Đã dừng trước khi nhập thông tin đăng nhập."
  ),
  "I can't see your student ID on the page anymore. Stopped before clicking a course.": (
    "Không còn thấy mã sinh viên trên trang. Đã dừng trước khi chọn học phần."
  ),
  "The browser opened an unexpected dialog. Check what it says.": (
    "Trình duyệt mở một hộp thoại ngoài dự kiến. Hãy đọc nội dung hộp thoại."
  ),
  "The portal has a message open. Check it before continuing.": "Cổng đăng ký đang hiện thông báo. Hãy kiểm tra trước khi tiếp tục.",
  "The portal is still loading or processing. Stopped here.": "Cổng đăng ký vẫn đang tải hoặc xử lý. Đã dừng lượt chạy.",
  "the portal's login form looks different. Check it before signing in.": (
    "Biểu mẫu đăng nhập trên cổng đã thay đổi. Hãy kiểm tra trước khi đăng nhập."
  ),
  "Couldn't get to the registration page. Check your login details and any messages from the portal.": (
    "Không mở được trang đăng ký. Kiểm tra thông tin đăng nhập và thông báo trên cổng."
  ),
  "Open the school’s registration page to continue.": "Mở trang đăng ký của trường để tiếp tục.",
  "The course tables haven't loaded or the portal changed their layout.": (
    "Các bảng học phần chưa tải xong hoặc đã thay đổi bố cục."
  ),
  "Clear the search filters in both tables so I can see every course.": (
    "Xóa bộ lọc tìm kiếm ở cả hai bảng để ứng dụng đọc được toàn bộ học phần."
  ),
  "The registered list looks incomplete. Its row count doesn't match the portal's total. Check the filters and pages.": (
    "Số dòng đã đăng ký khác với tổng số trên cổng. Hãy kiểm tra bộ lọc và các trang danh sách."
  ),
  "Couldn't read the schedule or seat count for {course}": "Không đọc được lịch hoặc số chỗ còn lại của {course}",
  "The portal shows the same course and group more than once. Check the list.": (
    "Cổng đăng ký hiển thị trùng mã học phần và nhóm. Hãy kiểm tra danh sách."
  ),
  "Some earlier selections disappeared: {courses}. Stopped here so you can check the list.": (
    "Một số nhóm đã chọn không còn trong danh sách: {courses}. Đã dừng để bạn kiểm tra."
  ),
  "Couldn't find the table's refresh button.": "Không tìm thấy nút làm mới của bảng.",
  "Couldn't tell which refresh button goes with this table.": "Không xác định được nút làm mới của bảng này.",
  "Refresh is still busy. Stopped here.": "Bảng vẫn đang làm mới. Đã dừng lượt chạy.",
  "The portal showed an unexpected message. Check the browser before continuing.": (
    "Cổng đăng ký hiện thông báo ngoài dự kiến. Hãy kiểm tra trình duyệt trước khi tiếp tục."
  ),
  "It's too early to register.": "Chưa đến giờ đăng ký.",
  "The registration window in your settings has ended.": "Đã hết thời gian đăng ký theo cài đặt.",
  "The registration window ended while waiting for the portal.": "Đã hết thời gian đăng ký trong lúc đợi cổng phản hồi.",
  "This row is already checked or I can't identify it. Refresh the registered list and check it.": (
    "Dòng này đã được tích chọn hoặc không xác định được. Hãy làm mới và kiểm tra danh sách đăng ký."
  ),
  "The course or group row changed, or I can't tell which row it is. Stopped here.": (
    "Dòng học phần hoặc nhóm đã thay đổi hoặc không xác định được. Đã dừng lượt chạy."
  ),
  "The portal said it failed, but the course is on the registered list. Check it before trying again.": (
    "Cổng báo lỗi nhưng học phần có trong danh sách đăng ký. Hãy kiểm tra trước khi thử lại."
  ),
  "Couldn't confirm registration for {course}. Check the portal before trying again. The journal still marks it as pending.": (
    "Chưa xác nhận được đăng ký {course}. Nhật ký vẫn ghi đang chờ. Hãy kiểm tra cổng đăng ký trước khi thử lại."
  ),
  "Pick a private folder you own. Use the folder's path directly.": (
    "Chọn một thư mục riêng do bạn sở hữu và dùng đường dẫn trực tiếp đến thư mục đó."
  ),
  "Couldn't read the run journal. Check it and the registered list on the portal.": (
    "Không đọc được nhật ký. Hãy kiểm tra nhật ký và danh sách đã đăng ký trên cổng."
  ),
  "The account lock needs to be a regular file you own.": "Tệp khóa tài khoản phải là tệp thường do bạn sở hữu.",
  "There's a pending submission in {journal}. Check the portal before continuing. Changing the opening date won't clear it.": (
    "Có lượt gửi đang chờ trong {journal}. Hãy kiểm tra cổng trước khi tiếp tục. Việc đổi ngày mở đăng ký không xóa lượt gửi này."
  ),
  "{course} disappeared from the registered list. Check the portal if you still want it.": (
    "{course} không còn trong danh sách đăng ký. Hãy kiểm tra cổng nếu bạn vẫn muốn học nhóm này."
  ),
  "Couldn't compare the old report with this run. Saving the current results.": (
    "Không đối chiếu được với báo cáo cũ. Đang lưu kết quả của lượt chạy này."
  ),
  "Couldn't download Playwright Chromium. Check your connection or pick an installed browser.": (
    "Không tải được Chromium của Playwright. Kiểm tra kết nối hoặc chọn một trình duyệt đã cài."
  ),
  "Using Playwright Chromium.": "Đang dùng Chromium của Playwright.",
  "Couldn't start a browser. Pick another one with --browser-executable.": (
    "Không mở được trình duyệt. Có thể chọn trình duyệt khác bằng tùy chọn --browser-executable."
  ),
  "Downloaded Playwright Chromium but couldn't start it.": "Đã tải Chromium của Playwright nhưng không mở được.",
  "initializing": "đang chuẩn bị",
  "waiting for the portal to show the login or registration page": "đang chờ trang đăng nhập hoặc đăng ký học phần",
  "checking login controls": "đang kiểm tra biểu mẫu đăng nhập",
  "submitting login": "đang gửi thông tin đăng nhập",
  "waiting for registration after login": "đang chờ trang đăng ký sau khi đăng nhập",
  "checking the registration page and your student ID": "đang kiểm tra trang đăng ký và mã sinh viên",
  "login and student identifier verified": "đã xác nhận đăng nhập và mã sinh viên",
}

TEMPLATES = [
  (
    re.compile(
      "".join(
        re.escape(part) + (f"(?P<{field}>.+?)" if field else "")
        for part, field, _, _ in Formatter().parse(source)
      )
    ),
    target,
  )
  for source, target in VI.items()
  if "{" in source
]


class Locale:
  def __init__(self):
    self.language = LANGUAGES[0]

  def __call__(self, text):
    if self.language == LANGUAGES[0]:
      return text
    if text in VI:
      return VI[text]
    if "\n" in text:
      return "\n".join(map(self, text.split("\n")))
    for pattern, target in TEMPLATES:
      if match := pattern.fullmatch(text):
        values = match.groupdict()
        for field in ("detail", "stage"):
          if field in values:
            values[field] = self(values[field])
        return target.format(**values)
    return text
